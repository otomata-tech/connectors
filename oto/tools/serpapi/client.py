"""
SerpAPI Client for Google Jobs and search.

Requires: requests
"""

import datetime as dt
import time
from typing import Optional, Dict, Any

import requests

from ..common.credentials import require

_HTTP_TIMEOUT = (10, 60)  # (connect, read) — never an unbounded wait

# Beyond this age, a payload was not observed "just now": it was
# served again from a cache. The bound is not arbitrary — `created_at` dates the
# moment SerpApi starts querying Google, and our READ timeout is
# 60 s: a genuinely fresh payload therefore cannot reach us older than
# ~60 s. 120 s leaves as much margin (clock drift included) while
# staying far below the one-hour retention of the two upstream caches.
_EMPTY_MAX_AGE = 120.0


class SerpAPIClient:
    """
    SerpAPI client — generic access to **all** SerpApi engines.

    `search(engine, params)` is the generic entry point (engine = 'google', 'bing',
    'google_trends', 'youtube', 'walmart', 'google_jobs'…). `search_jobs` /
    `get_job_details` are typed Google Jobs shortcuts built on top.
    """

    BASE_URL = "https://serpapi.com/search"

    def __init__(self, api_key: str = None):
        """
        Initialize SerpAPI client.

        Args:
            api_key: SerpAPI key
        """
        self.api_key = require(api_key, "SERPAPI_API_KEY")
        self.session = requests.Session()
        self._last_request = 0.0
        self._min_interval = 1.0

    def _rate_limit(self):
        """Ensure minimum time between requests."""
        elapsed = time.time() - self._last_request
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request = time.time()

    def _request(self, params: Dict) -> Dict:
        """Make API request."""
        self._rate_limit()
        params["api_key"] = self.api_key

        response = self.session.get(self.BASE_URL, params=params, timeout=_HTTP_TIMEOUT)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _payload_age(result: Dict) -> Optional[float]:
        """Age of the payload in seconds, or None if it does not date itself.

        `search_metadata.created_at` is the UTC timestamp SerpApi stamps at the
        moment it actually queries Google. Served again from a cache it stays
        FROZEN at that moment — so it is the only age witness the response body
        carries, and it holds for the two caches in series measured on
        2026-08-27 (SerpApi's and the Cloudflare edge in front of it, which
        copy it as-is).
        """
        created = (result.get("search_metadata") or {}).get("created_at")
        if not isinstance(created, str):
            return None
        try:
            stamp = dt.datetime.strptime(created.replace(" UTC", ""),
                                         "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
        stamp = stamp.replace(tzinfo=dt.timezone.utc)
        return (dt.datetime.now(dt.timezone.utc) - stamp).total_seconds()

    def _empty_must_be_fresh(
        self,
        params: Dict,
        result: Dict,
        results_key: str,
    ) -> tuple[Dict, Dict]:
        """Reject an EMPTY result that the cache served again; redo it once.

        Defect from usage signal #456 (2026-08-27): the same
        request returned 0 jobs, then 1 with `no_cache=True`. A zero had
        settled into the upstream cache, which served it again for an hour. This
        connector serves as an ACTIVITY INDICATOR — a memorized zero becomes a
        false and persistent absence there, which nothing distinguishes from a real absence
        since that is precisely what the field is supposed to be able to say.

        The cache is not ours (no cache in oto-core or in the
        backend): so we cannot decide NOT to store the empty in it. What
        we decide is not to READ it. Hence the deliberate asymmetry:
        - an empty result is only acceptable if **observed fresh**; stale, it
          is redone by forcing `no_cache` — the cost of a redone call is far
          lower than the cost of a false absence propagated across a whole campaign;
        - a NON-empty result keeps the right to the cache: a job list
          forty minutes old remains a valid activity signal, and it is
          what pays 0.0 s rather than the 5 to 20 s of upstream scraping.

        What the rule does NOT do, and must not be credited with: it does not
        protect against hammering. Measured on 2026-08-27 on the real API, a forced
        call does NOT replace the entry that ordinary calls will read —
        two consecutive calls on a durably empty query both
        re-scrape (6.3 s then 17.4 s). A repeated empty query therefore costs
        a full scrape each time, which is exactly what the
        `no_cache=True` that callers were already setting by hand cost. We stick with it:
        bounding this cost would require a cache of OUR OWN, which the server cannot
        carry (it builds one client per MCP call, so an instance cache
        would never serve). And the dominant case of a campaign — a company
        queried only once — pays nothing more: its empty is a genuine
        cache miss, hence already fresh, hence returned without a retry.

        A payload that does not date itself cannot certify its freshness: we
        treat it as stale. We lean toward the right answer, never toward
        the fast answer.
        """
        age = self._payload_age(result)
        refetched = False
        if not result.get(results_key) and (age is None or age > _EMPTY_MAX_AGE):
            params["no_cache"] = "true"
            result = self._request(params)
            age = self._payload_age(result)
            refetched = True
        return result, {
            "age_seconds": None if age is None else round(age),
            "refetched": refetched,
        }

    def _paginate(
        self,
        params: Dict,
        result: Dict,
        results_key: str,
        max_results: int,
    ) -> Dict:
        """Follow `serpapi_pagination.next_page_token` up to `max_results`.

        Concatenates `result[results_key]` page after page and truncates to
        `max_results`. Mutates and returns `result` (the last page serves as
        metadata). No-op if the engine returns no token.
        """
        all_items = list(result.get(results_key, []))
        while len(all_items) < max_results:
            next_token = result.get("serpapi_pagination", {}).get("next_page_token")
            if not next_token:
                break
            params["next_page_token"] = next_token
            result = self._request(params)
            new_items = result.get(results_key, [])
            if not new_items:
                break
            all_items.extend(new_items)

        result[results_key] = all_items[:max_results]
        return result

    def search(
        self,
        engine: str,
        params: Dict[str, Any] = None,
        max_results: int = None,
        results_key: str = None,
        **extra,
    ) -> Dict[str, Any]:
        """
        Generic SerpApi call — reach ANY engine.

        Args:
            engine: SerpApi engine id, e.g. 'google', 'bing', 'duckduckgo',
                'youtube', 'walmart', 'amazon', 'ebay', 'google_trends',
                'google_finance', 'google_flights', 'google_hotels',
                'google_events', 'google_jobs'… (full list: serpapi.com).
            params: engine-specific parameters (e.g. {'q': 'pizza', 'gl': 'us'}).
            max_results: if set with `results_key`, auto-paginate up to this many.
            results_key: the result array to paginate/cap (e.g. 'organic_results',
                'jobs_results'). Required to enable pagination.
            **extra: extra params merged into the query (convenience).

        Returns:
            Raw SerpApi JSON payload. When `results_key` is given, an EMPTY
            result is guaranteed to have been observed fresh (see
            `_empty_must_be_fresh`) and the payload carries an extra
            `oto_freshness` block: `age_seconds` (how old the answer SerpApi
            served actually is — 0 means just scraped, a large value means a
            cache served it) and `refetched` (whether we had to re-issue the
            call to avoid returning a stale empty).
        """
        payload: Dict[str, Any] = {"engine": engine}
        if params:
            payload.update(params)
        if extra:
            payload.update(extra)

        result = self._request(payload)
        if not results_key:
            # Without a named array, the client does not know which one carries the answer:
            # no freshness guarantee, no pagination — we do not GUESS what
            # "empty" means for an engine we do not know. Raw as-is.
            return result

        result, freshness = self._empty_must_be_fresh(payload, result, results_key)
        if max_results:
            result = self._paginate(payload, result, results_key, max_results)
        # Set AFTER pagination: `_paginate` rebinds `result` to the
        # LAST page read, and would take the block along with the old one.
        result["oto_freshness"] = freshness
        return result

    def search_jobs(
        self,
        query: str = None,
        company: str = None,
        location: str = None,
        country: str = None,
        language: str = "en",
        max_results: int = 100,
        no_cache: bool = False,
    ) -> Dict[str, Any]:
        """
        Search Google Jobs.

        Args:
            query: free-text job query (e.g. 'data engineer Paris', 'senior
                python remote'). Use this for general job sourcing.
            company: convenience shortcut — if `query` is omitted, searches
                "<company> jobs" (backward-compatible).
            location: Geographic location (e.g., 'Paris, France')
            country: Country code (e.g., 'fr', 'us')
            language: Language code
            max_results: Maximum results (handles pagination)
            no_cache: force a fresh scrape even when the answer is NOT empty.
                No longer needed for correctness: an empty `jobs_results` is
                always observed fresh (a cached empty is re-issued for you).

        Returns:
            Dict with a `jobs_results` array, plus an `oto_freshness` block
            saying how old the answer is and whether it had to be re-issued.
        """
        q = query or (f"{company} jobs" if company else None)
        if not q:
            raise ValueError("search_jobs requires `query` or `company`")

        params: Dict[str, Any] = {"q": q, "hl": language, "no_cache": str(no_cache).lower()}
        if location:
            params["location"] = location
        if country:
            params["gl"] = country

        return self.search(
            "google_jobs", params=params,
            max_results=max_results, results_key="jobs_results",
        )

    def get_job_details(self, job_id: str) -> Dict[str, Any]:
        """
        Get detailed job information.

        Args:
            job_id: Google Jobs job ID

        Returns:
            Detailed job information
        """
        return self.search("google_jobs_listing", params={"q": job_id})
