"""Firecrawl client — site scraping & crawling to markdown/JSON (firecrawl.dev).

API v2, Bearer auth. Five business surfaces:
- **scrape** (sync): one URL → markdown/html/links/screenshot, JS executed.
- **crawl** (async): a whole domain → job id, then pagination of the extracted pages.
- **map** (sync): fast discovery of all of a site's URLs (no content).
- **search** (sync): web search + full content of the results in one call.
- **extract** (async): structured extraction guided by a prompt/schema over N URLs.

Crawl and extract are **jobs**: the start call returns an `id`, and the status is re-read
while `status != "completed"`. Request bodies are passed as-is to the
API (the caller chooses its options) — see https://docs.firecrawl.dev.

Requires: requests
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

from ..common.credentials import require
from ..common import raise_for_upstream


class FirecrawlClient:
    """Firecrawl v2 client (https://api.firecrawl.dev/v2), Bearer auth `fc-…`."""

    BASE_URL = "https://api.firecrawl.dev/v2"

    def __init__(self, api_key: str = None):
        """
        Args:
            api_key: Firecrawl key.
        """
        self.api_key = require(api_key, "FIRECRAWL_API_KEY")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        })

    # --- transport ----------------------------------------------------------

    def _request(self, method: str, path: str, *, timeout: int = 120, **kwargs) -> Dict[str, Any]:
        url = path if path.startswith("http") else f"{self.BASE_URL}{path}"
        resp = self.session.request(method, url, timeout=timeout, **kwargs)
        raise_for_upstream(resp, service="firecrawl")
        return resp.json() if resp.content else {}

    @staticmethod
    def _compact(body: Dict[str, Any]) -> Dict[str, Any]:
        """Drop keys set to None — the API then applies ITS defaults."""
        return {k: v for k, v in body.items() if v is not None}

    # --- scrape (sync) ------------------------------------------------------

    def scrape(
        self,
        url: str,
        formats: Optional[List[Any]] = None,
        only_main_content: Optional[bool] = None,
        include_tags: Optional[List[str]] = None,
        exclude_tags: Optional[List[str]] = None,
        wait_for: Optional[int] = None,
        actions: Optional[List[Dict[str, Any]]] = None,
        headers: Optional[Dict[str, str]] = None,
        mobile: Optional[bool] = None,
        proxy: Optional[str] = None,
        max_age: Optional[int] = None,
        location: Optional[Dict[str, Any]] = None,
        timeout_ms: Optional[int] = None,
        timeout: int = 120,
    ) -> Dict[str, Any]:
        """POST /scrape — extract ONE page (JS executed on the Firecrawl side).

        Args:
            url: page to extract.
            formats: desired output(s) — `["markdown"]` by default on the API side.
                Accepts the API's object forms (e.g. `[{"type": "json",
                "schema": {...}}]` for structured extraction in one call,
                `[{"type": "screenshot", "fullPage": true}]`).
            only_main_content: strip nav/footer/ads (API default: true).
            include_tags / exclude_tags: CSS selectors to keep / remove.
            wait_for: ms to wait before capture (pages that populate via JS).
            actions: sequence of interactions before capture (click, write, scroll,
                wait…) — lets you get past a form or a cookie wall.
            max_age: max age (ms) of an accepted cached version — a cache hit
                is much faster and cheaper than a fresh scrape.
            timeout_ms: Firecrawl-side budget (max 60000 by default).
            timeout: local HTTP timeout (seconds).

        Returns: `{success, data: {markdown?, html?, links?, screenshot?, json?,
            metadata: {title, description, sourceURL, statusCode, …}}}`.
        """
        body = self._compact({
            "url": url,
            "formats": formats,
            "onlyMainContent": only_main_content,
            "includeTags": include_tags,
            "excludeTags": exclude_tags,
            "waitFor": wait_for,
            "actions": actions,
            "headers": headers,
            "mobile": mobile,
            "proxy": proxy,
            "maxAge": max_age,
            "location": location,
            "timeout": timeout_ms,
        })
        return self._request("POST", "/scrape", json=body, timeout=timeout)

    # --- crawl (async) ------------------------------------------------------

    def crawl(
        self,
        url: str,
        limit: Optional[int] = None,
        include_paths: Optional[List[str]] = None,
        exclude_paths: Optional[List[str]] = None,
        max_discovery_depth: Optional[int] = None,
        crawl_entire_domain: Optional[bool] = None,
        allow_subdomains: Optional[bool] = None,
        allow_external_links: Optional[bool] = None,
        sitemap: Optional[str] = None,
        delay: Optional[float] = None,
        prompt: Optional[str] = None,
        scrape_options: Optional[Dict[str, Any]] = None,
        webhook: Optional[Dict[str, Any]] = None,
        timeout: int = 60,
    ) -> Dict[str, Any]:
        """POST /crawl — start crawling a site. **Asynchronous**: returns a job id.

        Args:
            url: starting URL.
            limit: page cap (API default 10000 — setting it is the first
                protection against a surprise bill).
            include_paths / exclude_paths: path regexes to follow / ignore.
            max_discovery_depth: max discovery depth from the starting URL.
            crawl_entire_domain: go beyond the starting URL's tree.
            allow_subdomains / allow_external_links: widen beyond the host.
            sitemap: `"include"` (default) | `"skip"` | `"only"`.
            delay: seconds between two requests (politeness / anti-blocking).
            prompt: natural-language instruction from which Firecrawl derives the options.
            scrape_options: scrape options applied to EVERY page (same keys
                as `scrape`, camelCase).
            webhook: notification `{url, events: ["completed", …]}` instead of polling.

        Returns: `{success, id, url}` — `id` to pass to `crawl_status`.
        """
        body = self._compact({
            "url": url,
            "limit": limit,
            "includePaths": include_paths,
            "excludePaths": exclude_paths,
            "maxDiscoveryDepth": max_discovery_depth,
            "crawlEntireDomain": crawl_entire_domain,
            "allowSubdomains": allow_subdomains,
            "allowExternalLinks": allow_external_links,
            "sitemap": sitemap,
            "delay": delay,
            "prompt": prompt,
            "scrapeOptions": scrape_options,
            "webhook": webhook,
        })
        return self._request("POST", "/crawl", json=body, timeout=timeout)

    def crawl_status(
        self,
        crawl_id: Optional[str] = None,
        next_url: Optional[str] = None,
        timeout: int = 120,
    ) -> Dict[str, Any]:
        """GET /crawl/{id} — status + pages already extracted by a crawl.

        Args:
            crawl_id: id returned by `crawl`.
            next_url: `next` URL from a previous response — the response is
                capped at 10 MB, `next` is used to fetch the next slice.
                Passing `next_url` ignores `crawl_id`.

        Returns: `{status: scraping|completed|failed, total, completed,
            creditsUsed, expiresAt, next?, data: [pages]}`.
        """
        if not next_url and not crawl_id:
            raise ValueError("crawl_status: crawl_id or next_url required.")
        path = next_url or f"/crawl/{crawl_id}"
        return self._request("GET", path, timeout=timeout)

    def cancel_crawl(self, crawl_id: str, timeout: int = 60) -> Dict[str, Any]:
        """DELETE /crawl/{id} — stop a running crawl (stops credit consumption)."""
        return self._request("DELETE", f"/crawl/{crawl_id}", timeout=timeout)

    # --- map (sync) ---------------------------------------------------------

    def map_site(
        self,
        url: str,
        search: Optional[str] = None,
        limit: Optional[int] = None,
        sitemap: Optional[str] = None,
        include_subdomains: Optional[bool] = None,
        ignore_query_parameters: Optional[bool] = None,
        timeout: int = 120,
    ) -> Dict[str, Any]:
        """POST /map — list a site's URLs, WITHOUT extracting content.

        Much faster and cheaper than a crawl: used to spot the pages worth a
        scrape (`search` filters the URLs, e.g. "pricing", "carriere").

        Returns: `{success, links: [{url, title?, description?}]}`.
        """
        body = self._compact({
            "url": url,
            "search": search,
            "limit": limit,
            "sitemap": sitemap,
            "includeSubdomains": include_subdomains,
            "ignoreQueryParameters": ignore_query_parameters,
        })
        return self._request("POST", "/map", json=body, timeout=timeout)

    # --- search (sync) ------------------------------------------------------

    def search(
        self,
        query: str,
        limit: Optional[int] = None,
        sources: Optional[List[Any]] = None,
        categories: Optional[List[Any]] = None,
        tbs: Optional[str] = None,
        location: Optional[str] = None,
        country: Optional[str] = None,
        include_domains: Optional[List[str]] = None,
        exclude_domains: Optional[List[str]] = None,
        scrape_options: Optional[Dict[str, Any]] = None,
        timeout: int = 120,
    ) -> Dict[str, Any]:
        """POST /search — web search, with page content if requested.

        Args:
            query: query (supported operators: `"exact"`, `-excluded`, `site:`,
                `filetype:`). Max 500 characters.
            limit: number of results (default 10, max 100).
            sources: `[{"type": "web"|"news"|"images"}]` — web by default.
            categories: `[{"type": "github"|"research"|"pdf"}]`.
            tbs: time filter (e.g. `"qdr:w"` = last week).
            include_domains / exclude_domains: restrict to / exclude domains
                (mutually exclusive on the API side).
            scrape_options: if provided, each result is ALSO scraped (e.g.
                `{"formats": ["markdown"]}`) — otherwise only title/description/URL.

        Returns: `{success, data: {web?: [...], news?: [...], images?: [...]},
            creditsUsed}`.
        """
        body = self._compact({
            "query": query,
            "limit": limit,
            "sources": sources,
            "categories": categories,
            "tbs": tbs,
            "location": location,
            "country": country,
            "includeDomains": include_domains,
            "excludeDomains": exclude_domains,
            "scrapeOptions": scrape_options,
        })
        return self._request("POST", "/search", json=body, timeout=timeout)

    # --- extract (async) ----------------------------------------------------

    def extract(
        self,
        urls: List[str],
        prompt: Optional[str] = None,
        schema: Optional[Dict[str, Any]] = None,
        enable_web_search: Optional[bool] = None,
        show_sources: Optional[bool] = None,
        timeout: int = 60,
    ) -> Dict[str, Any]:
        """POST /extract — structured extraction over N URLs. **Asynchronous**.

        Args:
            urls: pages to process; a trailing `/*` extends to the whole site
                (e.g. `"https://acme.com/*"`).
            prompt: what to look for, in natural language.
            schema: JSON Schema of the desired output (more reliable than a prompt alone).
            enable_web_search: allow Firecrawl to fill in beyond the given URLs.

        Returns: `{success, id}` — to re-read via `extract_status`.
        """
        body = self._compact({
            "urls": urls,
            "prompt": prompt,
            "schema": schema,
            "enableWebSearch": enable_web_search,
            "showSources": show_sources,
        })
        return self._request("POST", "/extract", json=body, timeout=timeout)

    def extract_status(self, job_id: str, timeout: int = 120) -> Dict[str, Any]:
        """GET /extract/{id} — status + data of an extraction job.

        Returns: `{success, status: processing|completed|failed, data?, sources?}`.
        """
        return self._request("GET", f"/extract/{job_id}", timeout=timeout)
