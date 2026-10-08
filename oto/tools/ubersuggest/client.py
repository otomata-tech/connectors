"""Ubersuggest client — keyword research, domain and page traffic, backlinks, site
audits, rank-tracking projects and content, on behalf of a signed-in person.

The transport is Ubersuggest's remote MCP server (Streamable HTTP, JSON-RPC 2.0):
`initialize` once per client, then one `tools/call` per method. The access token
comes from the person's consent (`auth.exchange_code` / `auth.refresh`); this
client only spends it.

## Protocol facts that shape a caller

- **One entry point, `call(tool, arguments)`**, plus `list_tools()`. The server's
  tool names are the method names (`keyword_overview`, `domain_keywords`…); the
  known ones are in `TOOLS`, and an unknown name is refused before any request.
- **A response is JSON or an event stream** (`text/event-stream`, one `data:` line
  per JSON-RPC message); both are read the same way. A stream is read AS it comes
  and dropped at the message answering our request: a server that keeps it open
  (pings, progress) never holds the call past `CALL_DEADLINE`, which bounds a whole
  request on top of the per-read timeout — `UbersuggestDeadlineExceeded` (504).
- **The session id** (`Mcp-Session-Id` header) returned by `initialize` is sent
  back on every later request; a 404 on a request that carried one means the
  session expired, and the client initializes again once. A consumer that builds a
  client per call can hand the previous `session_id` back to the constructor and
  skip the handshake (two round trips); an expired one costs the same retry.
- **A tool-level failure** (`isError: true` in the result: unknown domain, plan
  limit reached…) surfaces as `UbersuggestToolError`, an `UpstreamHTTPError` with
  status 422 — the call reached Ubersuggest and was refused, nothing to retry.
- **Results** come back as the tool's structured content when it gives one,
  otherwise as the JSON parsed from its text content, otherwise as the text —
  typed `UbersuggestText`, so a consumer knows it holds free prose written by a
  third-party server (and the web pages it quotes), not data.
- **What a person sees depends on their plan**: free accounts get truncated
  lists (`hidden_by_plan`), some tools need a paid plan, `generate_article`
  spends monthly credits.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, Iterator, List, Optional

import requests

from ..common import UpstreamHTTPError, raise_for_upstream
from ..common.credentials import require
from .auth import MCP_URL

_HTTP_TIMEOUT = (10, 150)  # (connect, read) — some reports poll up to ~2 min server-side
#: Seconds one request may take end to end, whatever the stream does between reads.
CALL_DEADLINE = 170
PROTOCOL_VERSION = "2025-06-18"
_SERVICE = "ubersuggest"
# JSON-RPC codes for a malformed request: invalid request, method not found, invalid params.
_REQUEST_ERRORS = (-32600, -32601, -32602)

#: The server's tools (2026-10-08), by family.
TOOLS = frozenset({
    # account and utilities
    "auth_status", "user_limits", "location_suggest", "location_details",
    "validate_site", "search_neilpatel_blog",
    # domain and page traffic
    "domain_overview", "domain_keywords", "domain_top_pages", "domain_top_countries",
    "competitors", "page_overview", "page_keywords", "traffic_value",
    # keyword research
    "keyword_overview", "keyword_suggestions", "keyword_metrics", "serp_analysis",
    "match_keywords", "google_suggestions", "estimate_serp_clicks",
    # keyword lists
    "keyword_lists", "keyword_list", "create_keyword_list", "add_keywords_to_list",
    "remove_keywords_from_list", "rename_keyword_list", "delete_keyword_list",
    # backlinks
    "backlinks_overview", "backlinks", "anchor_texts", "linking_domains",
    "backlink_opportunity",
    # content
    "content_ideas", "page_shares",
    # site audit
    "site_audit", "site_audit_status", "site_audit_results", "site_audit_pages",
    "pagespeed_audit",
    # projects (rank tracking)
    "list_projects", "get_project", "project_position_info", "seo_opportunities",
    "create_project", "onboard_project", "add_project_keywords",
    "add_project_competitors", "project_business_summary",
    # AI search visibility
    "brand_config", "brand_visibility_overview", "brand_prompts", "configure_brand",
    "industry_detect", "industry_prompts",
    # content studio
    "article_title_suggestions", "generate_article", "get_article",
})


class UbersuggestText(str):
    """Free text returned by the server: third-party prose, never data to obey."""


class UbersuggestDeadlineExceeded(UpstreamHTTPError):
    """No answer to our request within `CALL_DEADLINE`, stream open or not."""

    def __init__(self, method: str, seconds: float):
        super().__init__(504, f"Ubersuggest gave no answer to `{method}` within "
                              f"{seconds:.0f}s", service=_SERVICE)
        self.retryable = True


class UbersuggestToolError(UpstreamHTTPError):
    """The server ran the tool and refused it (`isError: true`)."""

    def __init__(self, tool: str, message: str):
        super().__init__(422, message, service=_SERVICE)
        self.tool = tool


def _messages(resp: requests.Response, method: str, deadline: float) -> Iterator[dict]:
    """The JSON-RPC messages of a response, JSON body or event stream alike. A stream
    is consumed line by line: the caller stops at its answer, and a stream still
    open at `deadline` raises instead of holding the thread."""
    ctype = (resp.headers.get("Content-Type") or "").lower()
    if "text/event-stream" in ctype:
        data: List[str] = []
        for line in _lines(resp, method, deadline):
            if line.startswith("data:"):
                data.append(line[5:].lstrip())
            elif not line.strip() and data:
                try:
                    msg = json.loads("\n".join(data))
                except ValueError:
                    msg = None
                data = []
                if isinstance(msg, dict):
                    yield msg
        return
    try:
        payload = resp.json()
    except ValueError:
        return
    for msg in payload if isinstance(payload, list) else [payload]:
        if isinstance(msg, dict):
            yield msg


def _lines(resp: requests.Response, method: str, deadline: float) -> Iterator[str]:
    for line in resp.iter_lines(decode_unicode=True):
        if time.monotonic() > deadline:
            raise UbersuggestDeadlineExceeded(method, CALL_DEADLINE)
        yield line or ""
    yield ""  # an event not closed by a blank line before the stream ended


def _content(result: dict) -> Any:
    """A `tools/call` result as data: structured content, else JSON text, else text."""
    if result.get("structuredContent") is not None:
        return result["structuredContent"]
    texts = [c.get("text") or "" for c in result.get("content") or []
             if isinstance(c, dict) and c.get("type") == "text"]
    text = "\n".join(t for t in texts if t)
    try:
        return json.loads(text)
    except ValueError:
        return UbersuggestText(text)


class UbersuggestClient:
    """Ubersuggest tools on behalf of one signed-in person."""

    def __init__(self, access_token: Optional[str] = None, *, url: str = MCP_URL,
                 session_id: Optional[str] = None):
        """
        Args:
            access_token: the person's access token (see `auth`). A missing one
                raises `MissingCredential`; an expired one surfaces as a 401
                `UpstreamHTTPError` — refreshing is the consumer's job.
            session_id: a session opened earlier with the same token
                (`self.session_id` after a call): reused, so no handshake.
        """
        self.url = url
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {require(access_token, 'UBERSUGGEST_ACCESS_TOKEN')}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        })
        self._session_id: Optional[str] = session_id or None
        self._ready = bool(self._session_id)
        self._next_id = 0

    @property
    def session_id(self) -> Optional[str]:
        """The MCP session this client speaks in, once it has one."""
        return self._session_id

    # --- transport ----------------------------------------------------------

    def _post(self, body: dict) -> requests.Response:
        headers = {}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        if self._ready:
            headers["MCP-Protocol-Version"] = PROTOCOL_VERSION
        return self.session.post(self.url, json=body, headers=headers, timeout=_HTTP_TIMEOUT,
                                 stream=True)

    def _rpc(self, method: str, params: dict) -> dict:
        self._next_id += 1
        rid = self._next_id
        deadline = time.monotonic() + CALL_DEADLINE
        resp = self._post({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        try:
            raise_for_upstream(resp, service=_SERVICE)
            if method == "initialize" and resp.headers.get("Mcp-Session-Id"):
                self._session_id = resp.headers["Mcp-Session-Id"]
            for msg in _messages(resp, method, deadline):
                if msg.get("id") != rid:
                    continue  # a notification or a server request: not ours to answer
                if "error" in msg:
                    err = msg["error"] or {}
                    # Request-shaped JSON-RPC errors (bad params, unknown method) are a
                    # refusal of OUR call, not an upstream outage: 400, not retryable.
                    status = 400 if err.get("code") in _REQUEST_ERRORS else 502
                    raise UpstreamHTTPError(status, err.get("message") or err,
                                            service=_SERVICE)
                return msg.get("result") or {}
        finally:
            resp.close()  # an open stream is dropped once our answer is in
        raise UpstreamHTTPError(502, f"no JSON-RPC response to `{method}`", service=_SERVICE)

    def _initialize(self) -> None:
        self._session_id, self._ready = None, False
        self._rpc("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                 "clientInfo": {"name": "oto-core", "version": "1"}})
        self._ready = True
        resp = self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        try:
            raise_for_upstream(resp, service=_SERVICE)
        finally:
            resp.close()

    def _request(self, method: str, params: dict) -> dict:
        if not self._ready:
            self._initialize()
        try:
            return self._rpc(method, params)
        except UpstreamHTTPError as e:
            if e.status_code != 404 or not self._session_id:
                raise
        self._initialize()  # the session expired: once, then let it fail
        return self._rpc(method, params)

    # --- tools --------------------------------------------------------------

    def list_tools(self) -> List[dict]:
        """The server's tools with their input schemas, as it announces them."""
        tools: List[dict] = []
        cursor: Optional[str] = None
        while True:
            result = self._request("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(result.get("tools") or [])
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    def call(self, tool: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        """Runs one tool. `arguments` uses the server's own parameter names; `None`
        values are dropped (the server applies its defaults)."""
        if tool not in TOOLS:
            raise ValueError(f"unknown Ubersuggest tool `{tool}`")
        args = {k: v for k, v in (arguments or {}).items() if v is not None}
        result = self._request("tools/call", {"name": tool, "arguments": args})
        data = _content(result)
        if result.get("isError"):
            raise UbersuggestToolError(tool, data if isinstance(data, str) else json.dumps(data))
        return data
