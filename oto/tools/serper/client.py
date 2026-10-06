"""
Serper API Client for Google search (web, images, videos, news, places, maps,
shopping, scholar, patents, lens, reviews, autocomplete) and web scraping.

Serper exposes a family of Google endpoints under `https://google.serper.dev`
(POST, header `X-API-KEY`) + a scraper under `https://scrape.serper.dev`. They all
share the same base parameters (`q`, `gl`, `hl`, `location`, `num`,
`page`, `tbs`, `autocorrect`).

Requires: requests
"""

import math
import threading
import time
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse

import requests

from ..common.credentials import require

_HTTP_TIMEOUT = (10, 60)  # (connect, read) — never wait indefinitely

# SCRAPE waits less than everything else, and that is a measured choice (oto-backend#662).
# Over a campaign of 193 agent runs (2026-09-01): scrape p95 at 61.6 s,
# 58 timeouts at 60 s, 31% failure — and half of the failures were on URLs
# made up by the agent, which could NOT answer.
#
# ⚠️ What costs is not the wait, it is what the wait carries away: during a blocked
# minute, the agent's context cache expires. The median run that scraped
# cost 72,077 tokens against 35,798 without — DOUBLE — while the correlation with
# the reported volume stays weak (r = +0.177). A call that blocks for a minute can
# therefore cost tens of thousands of tokens FOR NOTHING.
#
# A page that has not answered in 15 s will not answer better in 60: the cap
# falls there. A DNS pre-check ("a nonexistent domain is known in milliseconds")
# was ruled out: our resolution is not Serper's, a refusal based on it
# cannot be guaranteed correct, and it would add a blocking wait on the hot
# path. The short cap already gives 4x without risking anything.
_SCRAPE_TIMEOUT = (5, 15)


class SerperClient:
    """
    Serper API client. One method per Google endpoint + scrape:
    - search           — web search (`/search`)
    - search_images    — images (`/images`)
    - search_videos    — videos (`/videos`)
    - search_news      — news (`/news`)
    - search_places    — places / Google Local (`/places`)
    - search_maps      — Google Maps (`/maps`)
    - search_reviews   — reviews of a place (`/reviews`)
    - search_shopping  — shopping (`/shopping`)
    - search_scholar   — Google Scholar (`/scholar`)
    - search_patents   — patents (`/patents`)
    - search_lens      — Google Lens / reverse image (`/lens`)
    - get_suggestions  — autocomplete (`/autocomplete`)
    - scrape_page      — scraping a page (`scrape.serper.dev`)
    """

    BASE_URL = "https://google.serper.dev"
    SCRAPE_URL = "https://scrape.serper.dev"

    def __init__(self, api_key: str = None):
        """
        Initialize Serper client.

        Args:
            api_key: Serper API key
        """
        self.api_key = require(api_key, "SERPER_API_KEY")
        self.session = requests.Session()
        self.session.headers.update({
            "X-API-KEY": self.api_key,
            "Content-Type": "application/json"
        })
        self._last_request = 0.0
        self._min_interval = 0.5
        self._rate_lock = threading.Lock()

    def _rate_limit(self):
        """Ensure minimum time between requests — including across THREADS.

        An instance shared between concurrent calls (the backend keeps one per
        key, oto#115): without a lock, two threads read the same `_last_request`,
        sleep the same amount and leave together — the limit no longer holds. The
        lock is held during the wait: that is what SPACES OUT the departures."""
        with self._rate_lock:
            elapsed = time.time() - self._last_request
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_request = time.time()

    def _post(self, url: str, json_data: Dict, label: str,
              timeout: Optional[tuple] = None) -> Dict:
        """POST + error handling. Surfaces Serper's error message rather than
        an opaque "400 Bad Request" (Serper returns 400 + {"message":...}
        for "Not enough credits", invalid key, etc.).

        `timeout` = `(connect, read)` in seconds; `None` = the client's
        default. Scrape sets a shorter one, see `_SCRAPE_TIMEOUT`."""
        self._rate_limit()
        response = self.session.post(url, json=json_data,
                                     timeout=timeout or _HTTP_TIMEOUT)
        if response.status_code >= 400:
            try:
                msg = response.json().get("message") or response.text
            except Exception:
                msg = response.text
            raise RuntimeError(f"Serper {label} {response.status_code}: {msg}")
        return response.json()

    def _request(self, endpoint: str, json_data: Dict) -> Dict:
        """Make API request to a `google.serper.dev` endpoint."""
        return self._post(f"{self.BASE_URL}{endpoint}", json_data, endpoint.lstrip("/"))

    @staticmethod
    def _credits_of(res: Any) -> int:
        """The credits Serper DEDUCTED for a response — its `credits` field.

        A method that paginates (`census_maps`, `reviews_all`) chains several
        requests, each billed by Serper according to what it cost (a Maps
        page of 100 results, a hard scrape): counting pages underestimates
        the spend. Falls back to 1 when the response does not say — a
        successful response costs at least one credit."""
        credits = res.get("credits") if isinstance(res, dict) else None
        if isinstance(credits, bool) or not isinstance(credits, (int, float)) or credits < 0:
            return 1
        return int(credits)

    @staticmethod
    def _common_payload(
        query: str,
        num: Optional[int] = None,
        page: Optional[int] = None,
        location: Optional[str] = None,
        country: Optional[str] = None,
        language: Optional[str] = None,
        tbs: Optional[str] = None,
        autocorrect: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Builds the payload shared by the Serper search endpoints.

        Maps the ergonomic names (country/language) to the Serper keys
        (`gl`/`hl`) and only includes the fields provided.
        """
        payload: Dict[str, Any] = {"q": query}
        if num is not None:
            payload["num"] = min(num, 100)
        if page is not None:
            payload["page"] = page
        if location:
            payload["location"] = location
        if country:
            payload["gl"] = country
        if language:
            payload["hl"] = language
        if tbs:
            payload["tbs"] = tbs
        if autocorrect is not None:
            payload["autocorrect"] = autocorrect
        return payload

    # ------------------------------------------------------------------ web

    def search(
        self,
        query: str,
        num: int = 10,
        page: int = 1,
        location: str = None,
        country: str = None,
        language: str = None,
        tbs: str = None,
        site_filter: str = None,
        autocorrect: bool = None,
    ) -> Dict[str, Any]:
        """
        Perform web search.

        Args:
            query: Search query
            num: Number of results (max 100)
            page: Page number
            location: Geographic location
            country: Country code (e.g., 'us', 'fr')
            language: Language code (e.g., 'en', 'fr')
            tbs: Google time filter (e.g., 'qdr:d' for past day)
            site_filter: Limit to site (e.g., 'linkedin.com')
            autocorrect: Toggle Google autocorrect

        Returns:
            Search results with 'organic' array
        """
        payload = self._common_payload(
            query=query if not site_filter else f"site:{site_filter} {query}",
            num=num, page=page, location=location, country=country,
            language=language, tbs=tbs, autocorrect=autocorrect,
        )
        return self._request("/search", payload)

    # --------------------------------------------------------------- images

    def search_images(
        self,
        query: str,
        num: int = 10,
        page: int = 1,
        location: str = None,
        country: str = None,
        language: str = None,
        tbs: str = None,
    ) -> Dict[str, Any]:
        """
        Search Google Images.

        Returns:
            Results with an 'images' array (title, imageUrl, source, link…)
        """
        payload = self._common_payload(
            query=query, num=num, page=page, location=location,
            country=country, language=language, tbs=tbs,
        )
        return self._request("/images", payload)

    # --------------------------------------------------------------- videos

    def search_videos(
        self,
        query: str,
        num: int = 10,
        page: int = 1,
        location: str = None,
        country: str = None,
        language: str = None,
        tbs: str = None,
    ) -> Dict[str, Any]:
        """
        Search Google Videos.

        Returns:
            Results with a 'videos' array (title, link, source, duration…)
        """
        payload = self._common_payload(
            query=query, num=num, page=page, location=location,
            country=country, language=language, tbs=tbs,
        )
        return self._request("/videos", payload)

    # ----------------------------------------------------------------- news

    def search_news(
        self,
        query: str,
        num: int = 10,
        page: int = 1,
        tbs: str = None,
        country: str = None,
        language: str = None,
        location: str = None,
    ) -> Dict[str, Any]:
        """
        Search Google News.

        Args:
            query: Search query
            num: Number of results (max 100)
            page: Page number
            tbs: Time filter (e.g., 'qdr:w' for past week)
            country: Country code
            language: Language code
            location: Geographic location

        Returns:
            News results with 'news' array
        """
        payload = self._common_payload(
            query=query, num=num, page=page, location=location,
            country=country, language=language, tbs=tbs,
        )
        return self._request("/news", payload)

    # --------------------------------------------------------------- places

    def search_places(
        self,
        query: str,
        num: int = 10,
        page: int = 1,
        location: str = None,
        country: str = None,
        language: str = None,
    ) -> Dict[str, Any]:
        """
        Search Google Local / Places (businesses near a location).

        Returns:
            Results with a 'places' array (title, address, rating, cid…)
        """
        payload = self._common_payload(
            query=query, num=num, page=page, location=location,
            country=country, language=language,
        )
        return self._request("/places", payload)

    # ----------------------------------------------------------------- maps

    def search_maps(
        self,
        query: str = None,
        ll: str = None,
        place_id: str = None,
        cid: str = None,
        num: int = 10,
        page: int = 1,
        country: str = None,
        language: str = None,
    ) -> Dict[str, Any]:
        """
        Search Google Maps.

        Args:
            query: Search query (e.g. "coffee shops")
            ll: Latitude/longitude + zoom anchor, format "@lat,lng,zoom"
                (e.g. "@40.6973709,-74.1444871,11z")
            place_id: Google place id to look up directly
            cid: Google customer id of a place
            num: Number of results (max 100)
            page: Page number
            country: Country code
            language: Language code

        Returns:
            Results with a 'places' array (rich Maps records)
        """
        payload: Dict[str, Any] = {}
        if query:
            payload["q"] = query
        if ll:
            payload["ll"] = ll
        if place_id:
            payload["placeId"] = place_id
        if cid:
            payload["cid"] = cid
        if num is not None:
            payload["num"] = min(num, 100)
        if page is not None:
            payload["page"] = page
        if country:
            payload["gl"] = country
        if language:
            payload["hl"] = language
        return self._request("/maps", payload)

    # ----------------------------------------------------------- maps census

    @staticmethod
    def _grid_anchors(
        center: str, radius_km: float, grid: int, zoom: int
    ) -> List[str]:
        """Tiles a square area (center ± radius_km) into `grid`×`grid` anchors
        `@lat,lng,zoomz`. km→degrees conversion: 1° lat ≈ 111 km, 1° lng ≈
        111·cos(lat) km. A single anchor if grid ≤ 1."""
        lat0, lng0 = (float(x) for x in center.split(","))
        grid = max(1, grid)
        if grid == 1:
            offsets = [0.0]
        else:
            offsets = [
                -radius_km + 2 * radius_km * i / (grid - 1) for i in range(grid)
            ]
        km_per_deg_lng = 111.0 * max(math.cos(math.radians(lat0)), 1e-6)
        anchors: List[str] = []
        for dlat_km in offsets:
            for dlng_km in offsets:
                lat = lat0 + dlat_km / 111.0
                lng = lng0 + dlng_km / km_per_deg_lng
                anchors.append(f"@{lat:.6f},{lng:.6f},{zoom}z")
        return anchors

    @staticmethod
    def _place_key(place: Dict[str, Any]) -> str:
        """Stable deduplication key for a Maps place: Google id if present
        (cid > placeId > fid), otherwise fall back to normalized title+address."""
        for k in ("cid", "placeId", "fid"):
            v = place.get(k)
            if v:
                return f"{k}:{v}"
        title = str(place.get("title", "")).strip().lower()
        address = str(place.get("address", "")).strip().lower()
        return f"ta:{title}|{address}"

    def census_maps(
        self,
        query: str,
        center: str = None,
        radius_km: float = 5.0,
        grid: int = 3,
        zoom: int = 14,
        ll_anchors: List[str] = None,
        max_pages: int = 3,
        country: str = None,
        language: str = None,
    ) -> Dict[str, Any]:
        """EXHAUSTIVE census of a type of business over an area.

        `search_maps` caps at ~20 results/call and biases toward the `ll`
        anchor point → it **silently undercounts**. This census
        removes both defects server-side: it **tiles** the area into a
        grid of geographic anchors, **paginates** each one, and **deduplicates** by
        place id. The returned `count` is therefore the real deduplicated total.

        Provide either `center` "lat,lng" (+ radius_km, grid) to tile the area,
        or explicit `ll_anchors` (which take precedence over tiling).

        Args:
            query: What is being enumerated (e.g. "self-service laundry").
            center: Area center "lat,lng" (required unless ll_anchors).
            radius_km: Half-width of the square area around the center (default 5).
            grid: Tiling density grid×grid (default 3 → 9 anchors).
            zoom: Maps zoom level per anchor (default 14).
            ll_anchors: Explicit "@lat,lng,zoomz" anchors (take precedence over tiling).
            max_pages: Max pages paginated per anchor (default 3).
            country: Country code (gl).
            language: Language code (hl).

        Returns:
            {query, count, places[], anchors_used, pages_fetched, credits_used} —
            `count` = deduplicated total, to be preferred over any count from a
            lone `search_maps`; `credits_used` = sum of the credits Serper
            deducted across all pages (what gets billed, not `pages_fetched`).
        """
        if not query:
            raise ValueError("census_maps requires a non-empty query")
        anchors = ll_anchors or (
            self._grid_anchors(center, radius_km, grid, zoom) if center else []
        )
        if not anchors:
            raise ValueError(
                "census_maps requires `center` ('lat,lng') or explicit `ll_anchors`"
            )

        seen: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []
        pages_fetched = 0
        credits_used = 0
        for anchor in anchors:
            for page in range(1, max_pages + 1):
                res = self.search_maps(
                    query=query, ll=anchor, num=100, page=page,
                    country=country, language=language,
                )
                pages_fetched += 1
                credits_used += self._credits_of(res)
                places = res.get("places") or []
                if not places:
                    break
                new = 0
                for p in places:
                    key = self._place_key(p)
                    if key in seen:
                        continue
                    seen[key] = p
                    order.append(key)
                    new += 1
                # Page entirely already seen → anchor exhausted or overlapping,
                # no point paginating further (deep pages diverge).
                if new == 0:
                    break

        return {
            "query": query,
            "count": len(order),
            "places": [seen[k] for k in order],
            "anchors_used": len(anchors),
            "pages_fetched": pages_fetched,
            "credits_used": credits_used,
        }

    # -------------------------------------------------------------- reviews

    def search_reviews(
        self,
        cid: str = None,
        fid: str = None,
        place_id: str = None,
        query: str = None,
        sort_by: str = None,
        topic_id: str = None,
        next_page_token: str = None,
        country: str = None,
        language: str = None,
    ) -> Dict[str, Any]:
        """
        Fetch reviews for a Google place.

        Identify the place by one of `cid` / `fid` / `place_id` (from a
        `search_places` / `search_maps` result), or by free-text `query`.

        Args:
            cid: Google customer id of the place
            fid: Google feature id of the place
            place_id: Google place id
            query: Free-text place lookup (alternative to ids)
            sort_by: 'mostRelevant' | 'newest' | 'highestRating' | 'lowestRating'
            topic_id: Filter reviews by topic id
            next_page_token: Pagination cursor from a previous response
            country: Country code
            language: Language code

        Returns:
            Results with a 'reviews' array + pagination token
        """
        payload: Dict[str, Any] = {}
        if cid:
            payload["cid"] = cid
        if fid:
            payload["fid"] = fid
        if place_id:
            payload["placeId"] = place_id
        if query:
            payload["q"] = query
        if sort_by:
            payload["sortBy"] = sort_by
        if topic_id:
            payload["topicId"] = topic_id
        if next_page_token:
            payload["nextPageToken"] = next_page_token
        if country:
            payload["gl"] = country
        if language:
            payload["hl"] = language
        return self._request("/reviews", payload)

    def reviews_all(
        self,
        cid: str = None,
        fid: str = None,
        place_id: str = None,
        query: str = None,
        sort_by: str = None,
        topic_id: str = None,
        max_reviews: int = 200,
        country: str = None,
        language: str = None,
    ) -> Dict[str, Any]:
        """ALL the reviews of a place — paginates `nextPageToken` until exhausted.

        `search_reviews` returns only one page (~10 reviews): a single call
        silently under-represents a place's reviews (the real total lives
        in `ratingCount` on the place side, not here). This method follows the
        `nextPageToken` cursor until there is no more page, or until the
        `max_reviews` cap (bounds the cost — a place can have thousands
        of reviews).

        Identify the place by `cid`/`fid`/`place_id` or `query` (like
        search_reviews). Returns {count, reviews[], pages_fetched, credits_used,
        truncated}. `truncated=True` = the cap cut before exhaustion;
        `credits_used` = sum of the credits Serper deducted across the pages.
        """
        collected: List[Dict[str, Any]] = []
        token: Optional[str] = None
        seen_tokens: set = set()
        pages = 0
        credits_used = 0
        while len(collected) < max_reviews:
            res = self.search_reviews(
                cid=cid, fid=fid, place_id=place_id, query=query,
                sort_by=sort_by, topic_id=topic_id, next_page_token=token,
                country=country, language=language,
            )
            pages += 1
            credits_used += self._credits_of(res)
            reviews = res.get("reviews") or []
            if not reviews:
                break
            collected.extend(reviews)
            token = res.get("nextPageToken")
            # No more cursor, or a cursor that repeats (anti-loop guard).
            if not token or token in seen_tokens:
                token = None
                break
            seen_tokens.add(token)
        return {
            "count": len(collected[:max_reviews]),
            "reviews": collected[:max_reviews],
            "pages_fetched": pages,
            "credits_used": credits_used,
            "truncated": len(collected) >= max_reviews and bool(token),
        }

    # ------------------------------------------------------------- shopping

    def search_shopping(
        self,
        query: str,
        num: int = 10,
        page: int = 1,
        location: str = None,
        country: str = None,
        language: str = None,
    ) -> Dict[str, Any]:
        """
        Search Google Shopping.

        Returns:
            Results with a 'shopping' array (title, price, source, rating…)
        """
        payload = self._common_payload(
            query=query, num=num, page=page, location=location,
            country=country, language=language,
        )
        return self._request("/shopping", payload)

    # -------------------------------------------------------------- scholar

    def search_scholar(
        self,
        query: str,
        num: int = 10,
        page: int = 1,
        country: str = None,
        language: str = None,
    ) -> Dict[str, Any]:
        """
        Search Google Scholar (academic papers).

        Returns:
            Results with an 'organic' array (title, publication, year, citedBy…)
        """
        payload = self._common_payload(
            query=query, num=num, page=page, country=country, language=language,
        )
        return self._request("/scholar", payload)

    # -------------------------------------------------------------- patents

    def search_patents(
        self,
        query: str,
        num: int = 10,
        page: int = 1,
        country: str = None,
        language: str = None,
    ) -> Dict[str, Any]:
        """
        Search Google Patents.

        Returns:
            Results with an 'organic'/'patents' array (title, inventor,
            assignee, publicationNumber…)
        """
        payload = self._common_payload(
            query=query, num=num, page=page, country=country, language=language,
        )
        return self._request("/patents", payload)

    # ----------------------------------------------------------------- lens

    def search_lens(
        self,
        url: str,
        country: str = None,
        language: str = None,
    ) -> Dict[str, Any]:
        """
        Google Lens — reverse image search from an image URL.

        Args:
            url: Public URL of the image to analyse
            country: Country code
            language: Language code

        Returns:
            Results with an 'organic' array of visual matches
        """
        payload: Dict[str, Any] = {"url": url}
        if country:
            payload["gl"] = country
        if language:
            payload["hl"] = language
        return self._request("/lens", payload)

    # --------------------------------------------------------------- scrape

    # Hosts that SYSTEMATICALLY refuse a server-side scrape (login wall or
    # permanent anti-bot). Sending Serper there costs ~45 s — its own timeout — to
    # come back empty-handed every time: measured over 4 days of logs, six failures
    # at 45-48 s, four of them on LinkedIn profiles. Better to refuse right away,
    # stating the FACT — the source is closed to extraction — without prescribing a tool:
    # this client does not know the toolset served to the caller (a CLI, a
    # published endpoint serving an inclusion list…), and a refusal that names an absent door
    # leaves the agent with an intent and no destination (oto-backend#632, family of
    # #613). The previous text named `unipile_*` — a family that no longer even exists
    # under that name.
    #
    # The list stays SHORT and only holds structural refusals. A site that
    # sometimes blocks has no business there: this guard removes a useless wait, it must not
    # become a blacklist that deprives us of a scrape that would have worked.
    _NEVER_SCRAPABLE = {
        "linkedin.com": "LinkedIn profiles and pages cannot be read by extraction "
                        "(login wall); if your toolset carries a connected "
                        "LinkedIn account, go through it.",
        "instagram.com": "Instagram requires a session; go through the messaging "
                         "connector or another source.",
        "facebook.com": "Facebook requires a session; look for another source.",
        "x.com": "X requires a session; look for another source.",
        "twitter.com": "X requires a session; look for another source.",
    }

    @classmethod
    def _refuses_scraping(cls, url: str) -> Optional[str]:
        """The reason to refuse outright, or None. Compares on the registrable
        domain to cover subdomains (`fr.`, `uk.`, `www.`)."""
        try:
            host = (urlparse(url).hostname or "").lower()
        except ValueError:
            return None
        for domain, why in cls._NEVER_SCRAPABLE.items():
            if host == domain or host.endswith("." + domain):
                return why
        return None

    def scrape_page(
        self,
        url: str,
        include_markdown: bool = False,
        timeout_s: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Scrape a web page. Waits 15 s at most by default, not 60.

        Args:
            url: URL to scrape
            include_markdown: Include markdown version
            timeout_s: seconds to wait for the page, 1-60. Default 15 — a page
                that has not answered in 15 s will not answer better in 60, and
                the wait costs the caller far more than the wait itself (an
                agent's context cache expires while it blocks).

        Returns:
            Page data with text, metadata, and JSON-LD

        Raises:
            RuntimeError: if the host structurally refuses server-side scraping — the
                message states the fact (source closed to extraction) without prescribing a
                tool: the caller decides with the toolset it has.
            requests.Timeout: the page did not answer within the delay. This is a
                NORMAL FAILURE, not an outage: half of the measured timeouts
                were on addresses that could not answer.
        """
        why = self._refuses_scraping(url)
        if why:
            raise RuntimeError(f"Serper scrape refused for {url}: {why}")
        payload: Dict[str, Any] = {"url": url}
        if include_markdown:
            payload["includeMarkdown"] = True
        delai = _SCRAPE_TIMEOUT
        if timeout_s is not None:
            borne = max(1, min(int(timeout_s), 60))
            delai = (min(_SCRAPE_TIMEOUT[0], borne), borne)
        return self._post(self.SCRAPE_URL, payload, "scrape", timeout=delai)

    # --------------------------------------------------------- autocomplete

    def autocomplete(
        self,
        query: str,
        country: str = None,
        language: str = None,
    ) -> Dict[str, Any]:
        """
        Raw autocomplete endpoint (full Serper response).

        Returns:
            Results with a 'suggestions' array of {value} objects
        """
        payload: Dict[str, Any] = {"q": query}
        if country:
            payload["gl"] = country
        if language:
            payload["hl"] = language
        return self._request("/autocomplete", payload)

    def get_suggestions(self, query: str, country: str = None) -> List[str]:
        """
        Get search autocomplete suggestions (flattened to a list of strings).

        Args:
            query: Base query
            country: Country code

        Returns:
            List of suggested queries
        """
        try:
            result = self.autocomplete(query, country=country)
            suggestions = result.get("suggestions", [])
            return [s.get("value", "") for s in suggestions if s.get("value")]
        except Exception:
            return []

    # ---------------------------------------------------------------- batch

    def batch_search(
        self,
        queries: List[str],
        num_per_query: int = 10,
        **kwargs,
    ) -> List[Dict[str, Any]]:
        """
        Perform multiple web searches.

        Args:
            queries: List of queries
            num_per_query: Results per query
            **kwargs: Additional search params

        Returns:
            List of results per query
        """
        results = []
        for query in queries:
            try:
                result = self.search(query, num=num_per_query, **kwargs)
                results.append({"query": query, "results": result})
            except Exception as e:
                results.append({"query": query, "error": str(e), "results": {}})
        return results
