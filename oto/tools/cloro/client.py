"""
Cloro client — AI-search monitoring & SERP-in-JSON (cloro.dev).

A single API (Bearer) that queries AI engines (ChatGPT, Gemini, Perplexity,
Copilot, Grok, Google AI Mode) and Google (organic SERP + AI Overview + PAA,
Google News), and returns structured JSON (text/markdown + sources/citations).
Business use: "AI SEO" brand monitoring (what AI engines say about a
brand/product), competitive intelligence, clean SERP as JSON.

Requires: requests
"""

from typing import Any, Dict, Optional

import requests

from ..common.credentials import require


class CloroClient:
    """cloro.dev client. Bearer auth; sync endpoints `POST /v1/monitor/{provider}`.

    AI engine calls can take ~30-45 s (generous default timeout).
    """

    BASE_URL = "https://api.cloro.dev/v1"

    # Conversational AI engines (`prompt` body).
    AI_PROVIDERS = ("chatgpt", "gemini", "grok", "copilot", "perplexity", "aimode")

    def __init__(self, api_key: str = None):
        """
        Initialize Cloro client.

        Args:
            api_key: Cloro API key.
        """
        self.api_key = require(api_key, "CLORO_API_KEY")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        })

    def _post(self, path: str, body: Dict[str, Any], timeout: int) -> Dict[str, Any]:
        response = self.session.post(f"{self.BASE_URL}{path}", json=body, timeout=timeout)
        response.raise_for_status()
        return response.json()

    def monitor(
        self,
        provider: str,
        prompt: str,
        country: Optional[str] = None,
        include: Optional[Dict[str, bool]] = None,
        timeout: int = 180,
    ) -> Dict[str, Any]:
        """Query an AI engine (`provider` ∈ AI_PROVIDERS) with `prompt`.

        Args:
            provider: 'chatgpt' | 'gemini' | 'perplexity' | 'copilot' | 'grok' | 'aimode'.
            prompt: question/query (1–10,000 characters).
            country: ISO country code (e.g. 'US', 'FR').
            include: extraction flags (e.g. {'markdown': True, 'searchQueries': True}).

        Returns: Cloro payload `{success, result: {text, markdown, sources, ...}}`.
        """
        body: Dict[str, Any] = {"prompt": prompt}
        if country:
            body["country"] = country
        if include:
            body["include"] = include
        return self._post(f"/monitor/{provider}", body, timeout)

    def google(
        self,
        query: str,
        country: Optional[str] = None,
        include: Optional[Dict[str, bool]] = None,
        timeout: int = 120,
    ) -> Dict[str, Any]:
        """Google SERP en JSON via Cloro (organique + AI Overview + People Also Ask).

        Args:
            query: search query.
            country: ISO country code.
            include: flags, e.g. {'aiOverview': True, 'organicResults': True,
                'peopleAlsoAsk': True}.
        """
        body: Dict[str, Any] = {"query": query}
        if country:
            body["country"] = country
        if include:
            body["include"] = include
        return self._post("/monitor/google", body, timeout)

    def google_news(
        self,
        query: str,
        country: Optional[str] = None,
        timeout: int = 120,
    ) -> Dict[str, Any]:
        """Google News en JSON via Cloro.

        Args:
            query: query.
            country: ISO country code.
        """
        body: Dict[str, Any] = {"query": query}
        if country:
            body["country"] = country
        return self._post("/monitor/google/news", body, timeout)
