"""Clay client — Public API (routines, search, tables) + table webhooks.

Clay is a GTM enrichment platform: **tables** where each column
can call a data provider or a model, and **routines** (Clay-managed functions,
custom functions, Workflows) executable outside the UI.

Two surfaces, two auths, two classes:

- `ClayClient` — the **Public API** (`https://api.clay.com/public/v0`), header
  `clay-api-key` (PERSONAL key, tied to a Clay user and their workspace
  access; created in Settings → Account → API keys). Covers the 16 operations
  of the published OpenAPI (https://developers.clay.com/openapi.json), 1 method = 1
  endpoint. Everything there is READ or execution: the API can neither create a
  table nor write a row into one.
- `ClayTableWebhook` — a **table's incoming webhook** ("Monitor
  webhook" source added in the Clay UI): the ONLY way to write rows into a
  Clay table from outside. One JSON POST = one row. No API key: the URL
  itself (plus an optional token in a header) is the right to write. No API
  creates this webhook — it is copied from the UI, hence `parse_curl` to accept the
  cURL command Clay displays as is.

Costs: each call consumes the Clay credits of the key's workspace, like the same
work done in the UI. Rate limit per workspace: 429 + `Retry-After` (seconds),
passed through as is in `UpstreamHTTPError.body` (key `retry_after`).

Docs: https://developers.clay.com — https://university.clay.com/docs/webhook-integration-guide

Requires: requests
"""
from __future__ import annotations

import re
import shlex
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests

from ..common.credentials import require
from ..common import UpstreamHTTPError, raise_for_upstream

SOURCE_TYPES = ("people", "companies")

# Header of a table webhook's optional token ("authentication token").
WEBHOOK_AUTH_HEADER = "x-clay-webhook-auth"


def _raise(resp: Any) -> None:
    """`raise_for_upstream`, plus a 429's `Retry-After` in the raised body."""
    if resp.status_code == 429:
        try:
            body = resp.json()
        except Exception:
            body = {"message": resp.text}
        if not isinstance(body, dict):
            body = {"message": body}
        retry = resp.headers.get("Retry-After")
        if retry:
            body["retry_after"] = retry
        raise UpstreamHTTPError(429, body, service="clay")
    raise_for_upstream(resp, service="clay")


class ClayClient:
    """Clay Public API client — 1 method = 1 endpoint."""

    BASE_URL = "https://api.clay.com/public/v0"

    def __init__(self, api_key: Optional[str] = None, timeout: tuple = (10, 40)):
        """Initialize the client.

        Args:
            api_key: Clay Public API key.
            timeout: (connect, read) in seconds.
        """
        self.api_key = require(api_key, "CLAY_API_KEY")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "clay-api-key": self.api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    def _request(self, method: str, path: str, **kwargs) -> Any:
        resp = self.session.request(
            method, f"{self.BASE_URL}{path}", timeout=self.timeout, **kwargs)
        _raise(resp)
        return resp.json() if resp.content else {}

    # --- account ---------------------------------------------------------------

    def get_me(self) -> Dict[str, Any]:
        """`GET /me` — the key's user and workspace (`{user, workspace}`)."""
        return self._request("GET", "/me")

    def get_credit_balance(self) -> Dict[str, Any]:
        """`GET /credits/balance` — workspace balances
        (`{balance, action_execution_balance}`)."""
        return self._request("GET", "/credits/balance")

    # --- routines -------------------------------------------------------------

    def run_routine(
        self,
        routine_id: str,
        items: List[Dict[str, Any]],
        webhook_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """`POST /routines/{routine_id}/run` — runs a routine on 1 to 100 items.

        Always ASYNCHRONOUS: returns `{routine_run_id, status: "in_progress"}`,
        never the results — read them with `get_run_results`.

        Args:
            routine_id: e.g. `function:t_abc123` (custom function). No endpoint
                lists routines: the id is copied from the Clay app or CLI.
            items: `[{id, inputs: {...}}]` — `id` (≤ 64 chars) is returned with the
                item's result to match them up.
            webhook_id: registered Clay webhook to notify at the end of the run.
        """
        body: Dict[str, Any] = {"items": items}
        if webhook_id:
            body["webhook_id"] = webhook_id
        return self._request("POST", f"/routines/{routine_id}/run", json=body)

    def get_run_results(
        self,
        routine_run_id: str,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """`GET /routines/run/{routine_run_id}/results` — progress + results.

        `{routine_run_id, status, finished, total, data, cursor}`. `status` becomes
        `complete` when the run is done; a complete run can contain `failed`
        items. `cursor` present = next page (limit 1-100, default 20)."""
        params: Dict[str, Any] = {}
        if cursor:
            params["cursor"] = cursor
        if limit:
            params["limit"] = limit
        return self._request(
            "GET", f"/routines/run/{routine_run_id}/results", params=params)

    def create_batch_upload_url(self, routine_id: str) -> Dict[str, Any]:
        """`POST /routines/{routine_id}/run-batch/upload-url` — presigned PUT URL
        for the input JSONL file (`{file_id, upload_url}`)."""
        return self._request(
            "POST", f"/routines/{routine_id}/run-batch/upload-url", json={})

    def upload_batch_file(self, upload_url: str, jsonl: str) -> None:
        """PUT the JSONL to the presigned URL (outside the API: no key header)."""
        resp = requests.put(upload_url, data=jsonl.encode("utf-8"),
                            timeout=(10, 120))
        _raise(resp)

    def start_batch_run(
        self,
        routine_id: str,
        file_id: str,
        webhook_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """`POST /routines/{routine_id}/run-batch/start` — async run on the
        uploaded JSONL (`{routine_run_id, status}`)."""
        body: Dict[str, Any] = {"file_id": file_id}
        if webhook_id:
            body["webhook_id"] = webhook_id
        return self._request(
            "POST", f"/routines/{routine_id}/run-batch/start", json=body)

    def get_batch_run_results(self, routine_run_id: str) -> Dict[str, Any]:
        """`GET /routines/run-batch/{routine_run_id}/results` — progress and
        results of a batch run."""
        return self._request(
            "GET", f"/routines/run-batch/{routine_run_id}/results")

    # --- search (Clay's GTM database) ---------------------------------------

    def list_search_fields(self, source_type: str) -> Dict[str, Any]:
        """`GET /search/filters-mode/fields` — filters available for
        `people` or `companies` (`{source_type, fields, guidance}`)."""
        return self._request(
            "GET", "/search/filters-mode/fields",
            params={"source_type": source_type})

    def create_filters_search(
        self, source_type: str, filters: Dict[str, Any],
    ) -> Dict[str, Any]:
        """`POST /search/filters-mode` — creates a structured-filters search
        (`{search_id}`). No results before `run_filters_search`."""
        return self._request(
            "POST", "/search/filters-mode",
            json={"source_type": source_type, "filters": filters})

    def run_filters_search(
        self, search_id: str, limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """`POST /search/filters-mode/{search_id}/run` — NEXT page of
        the iterator (`{data, has_more, period_quota}`, limit 1-500, default 20).
        Each call advances: calling again = the following page."""
        body = {"limit": limit} if limit else {}
        return self._request(
            "POST", f"/search/filters-mode/{search_id}/run", json=body)

    def get_query_reference(self) -> Dict[str, Any]:
        """`GET /search/query-mode/reference` — Clay query grammar."""
        return self._request("GET", "/search/query-mode/reference")

    def create_query_search(self, query: str) -> Dict[str, Any]:
        """`POST /search/query-mode` — creates a search from a Clay query
        (`{search_id, source_type}`)."""
        return self._request("POST", "/search/query-mode", json={"query": query})

    def run_query_search(
        self, search_id: str, limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """`POST /search/query-mode/{search_id}/run` — next page (limit
        1-500, default 20)."""
        body = {"limit": limit} if limit else {}
        return self._request(
            "POST", f"/search/query-mode/{search_id}/run", json=body)

    # --- tables (Enterprise) --------------------------------------------------

    def query_tables(
        self,
        query: Dict[str, Any],
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """`POST /tables/query` — structured query over one or more known
        tables (`{data, fields, cursor, truncated}`, limit 1-100, default 50).

        Enterprise plan only (table sync via API). Read-only. The
        traversal follows last-updated order: a row modified during
        the scan can come back — deduplicate by id."""
        body: Dict[str, Any] = {"query": query}
        if cursor:
            body["cursor"] = cursor
        if limit:
            body["limit"] = limit
        return self._request("POST", "/tables/query", json=body)

    # --- workflow runs (beta) ---------------------------------------------

    def get_runs_query_reference(self) -> Dict[str, Any]:
        """`GET /workflows/runs/query/reference` — run search grammar."""
        return self._request("GET", "/workflows/runs/query/reference")

    def query_workflow_runs(
        self,
        query: str,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """`POST /workflows/runs/query` — run search (beta)."""
        body: Dict[str, Any] = {"query": query}
        if cursor:
            body["cursor"] = cursor
        if limit:
            body["limit"] = limit
        return self._request("POST", "/workflows/runs/query", json=body)


# --- table webhooks ------------------------------------------------------


def is_clay_webhook_url(url: str) -> bool:
    """True if `url` is an https URL on a `clay.com` host (or subdomain).

    Destination guard: a "Clay webhook" that pointed elsewhere would turn
    oto into a POST relay to any host."""
    try:
        p = urlparse((url or "").strip())
    except ValueError:
        return False
    host = (p.hostname or "").lower()
    return p.scheme == "https" and (host == "clay.com" or host.endswith(".clay.com"))


def parse_curl(text: str) -> Dict[str, Optional[str]]:
    """Extract `{webhook_url, auth_token}` from a cURL command copied from Clay.

    Also accepts a bare URL (then `auth_token` = None). Tolerates trailing `\\`
    line continuations and single/double quotes. Raises `ValueError` if no
    https URL is found."""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("empty input")
    if not raw.lower().startswith("curl"):
        return {"webhook_url": raw, "auth_token": None}
    # Line-continuation `\` become blanks — including when a
    # one-line field already removed the break (`'url'\ -H …`). No Clay URL or token
    # carries a backslash; the `-d` body, for its part, is ignored.
    flat = re.sub(r"\\[ \t]*(?:\r?\n)?", " ", raw)
    try:
        tokens = shlex.split(flat)
    except ValueError as e:
        raise ValueError(f"unparseable cURL command: {e}") from None
    url: Optional[str] = None
    token: Optional[str] = None
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in ("-H", "--header") and i + 1 < len(tokens):
            name, _, value = tokens[i + 1].partition(":")
            if name.strip().lower() == WEBHOOK_AUTH_HEADER:
                token = value.strip() or None
            i += 2
            continue
        if t in ("-d", "--data", "--data-raw", "--data-binary", "-X", "--request"):
            i += 2
            continue
        if url is None and re.match(r"^https?://", t):
            url = t
        i += 1
    if not url:
        raise ValueError("no URL found in the cURL command")
    return {"webhook_url": url, "auth_token": token}


class ClayTableWebhook:
    """Writes rows into ONE Clay table via its incoming webhook.

    One `push` = one POST = one row. No batching on Clay's side: the caller loops.
    Missing or wrong token on a protected webhook → 401.
    Clay cap: 50,000 sends per webhook (except Enterprise "auto-delete"),
    counter not reset by deleting rows — beyond it, a webhook must be
    recreated in the UI."""

    def __init__(self, webhook_url: str, auth_token: Optional[str] = None,
                 timeout: tuple = (5, 10)):
        if not is_clay_webhook_url(webhook_url):
            raise ValueError("not a Clay webhook URL (https://…clay.com/… expected)")
        self.webhook_url = webhook_url.strip()
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        if auth_token:
            self.session.headers[WEBHOOK_AUTH_HEADER] = auth_token

    def push(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """POST one row. The whole object lands in the table's Webhook column
        (its keys are mapped to columns on Clay's side); a JSON array is not
        split and makes only one row."""
        resp = self.session.post(self.webhook_url, json=row, timeout=self.timeout)
        _raise(resp)
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {"response": resp.text[:200]}
