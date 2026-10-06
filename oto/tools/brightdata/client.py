"""
Bright Data client — scaffold (empty shell).

The connector is wired on the platform side (registry + key), but the products are
**not yet implemented**. This class sets up authentication and HTTP access;
the product methods remain to be written.

Products to wire up (unified endpoint `https://api.brightdata.com/request`, POST,
Bearer auth):
- **SERP API** — structured search results (Google/Bing…). Body:
  `{"zone": <serp_zone>, "url": "https://www.google.com/search?q=...", "format": "raw"}`
  + `brd_json=1` (query param of the `url`) or `"data_format": "parsed_light"` for
  parsed JSON; `"data_format": "markdown"` for Markdown.
- **Web Unlocker** — fetch of any protected URL (anti-bot) → raw HTML
  or Markdown. Body: `{"zone": <unlocker_zone>, "url": ..., "format": "raw"}`.
- **Web Scraper / Datasets** — structured datasets (LinkedIn, Amazon…) via an
  asynchronous trigger→snapshot flow (endpoints `/datasets/v3/*`, polling).

Requires: requests
"""

from typing import Any, Dict

import requests

from ..common.credentials import require

_HTTP_TIMEOUT = (10, 60)  # (connect, read) — never an unbounded wait


class BrightDataClient:
    """Bright Data client (scaffold). Bearer auth + `/request` endpoint set up;
    no public product method for now (see the module docstring)."""

    BASE_URL = "https://api.brightdata.com/request"

    def __init__(self, api_key: str = None):
        """
        Initialize Bright Data client.

        Args:
            api_key: Bright Data API token.
        """
        self.api_key = require(api_key, "BRIGHTDATA_API_KEY")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        })

    def _post(self, payload: Dict[str, Any]) -> requests.Response:
        """POST on the unified `/request` endpoint. Low-level helper ready for the
        future product methods (SERP / Web Unlocker)."""
        response = self.session.post(self.BASE_URL, json=payload, timeout=_HTTP_TIMEOUT)
        response.raise_for_status()
        return response

    # TODO — product methods to implement (see the module docstring):
    #   serp(query, engine="google", parse=True, ...)  -> parsed SERP JSON
    #   unlock(url, data_format=None, ...)              -> HTML / Markdown
    #   dataset_trigger(...) / dataset_snapshot(...)    -> async datasets
