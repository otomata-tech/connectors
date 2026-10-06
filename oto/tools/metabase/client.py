"""Metabase API client — read and query: collections, saved questions (cards),
dashboards, database schema, and native SQL or MBQL queries.

Auth = an **API key** (Admin → Settings → Authentication → API keys, Metabase
≥ 49), sent in the `x-api-key` header. A key belongs to a group: it sees what
that group sees, and nothing else — a 403 means the group lacks the
permission, not that the key is wrong (that is a 401).

The credential is two values: the instance URL and the API key. There is no
default host — Metabase is self-hosted or served at an address of the
customer's own, so the URL is always the caller's. HTTPS only: an `http://`
instance is refused (the key would travel in clear), unless the caller passes
`allow_http=True` for a local instance. Paths and payloads follow the public
API reference (https://www.metabase.com/docs/latest/api).

What the caller needs to know, and cannot guess:

- **Redirects are not followed.** `requests` keeps a custom header such as
  `x-api-key` across a cross-host redirect: following one would hand the key
  to whatever host the redirect names. Any 3xx is raised — a redirect as
  `MetabaseRedirect`, carrying its target, so the stored URL gets fixed once.
- **A failed query is not an HTTP error.** The query endpoints answer `202`
  with `{"status": "failed", "error": ...}` when the database rejects the
  query; the client raises it as `MetabaseQueryError` (status 422) instead of
  returning it as a result.
- **Results are capped**: an ad-hoc or saved-question query returns at most
  2,000 rows (`row_count`, `data.rows`, `data.cols`); the rest is not fetched.
  Aggregate in the query rather than paging through rows. `records()` zips
  `cols` and `rows` into dicts.
- **Native SQL runs with the database connection's own rights**, not the
  key's: Metabase checks that the key's group may write native queries on that
  database, the database decides what a statement may do.

Not here, on purpose: writes (cards, dashboards, collections, users,
permissions, settings) and the bulk export endpoints.

Requires: requests
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Union
from urllib.parse import urlsplit

import requests

from ..common import raise_for_upstream
from ..common.credentials import require
from ..common.errors import UpstreamHTTPError

#: (connect, read) — a query can take a while, never an unbounded wait.
_HTTP_TIMEOUT = (10, 120)
_SERVICE = "metabase"

#: Search models (`/api/search?models=`).
SEARCH_MODELS = ("card", "dataset", "metric", "dashboard", "collection",
                 "table", "database", "segment", "action", "indexed-entity")

#: Card list filters (`/api/card?f=`).
CARD_FILTERS = ("all", "mine", "bookmarked", "database", "table", "using_model",
                "using_metric", "using_segment", "archived")

#: Pages of the Metabase app whose URL gets pasted for the instance URL.
_APP_SEGMENTS = ("/api", "/dashboard", "/question", "/collection", "/browse",
                 "/admin", "/model", "/auth")


class MetabaseRedirect(ValueError):
    """The instance answered with a redirect: the stored URL is not the one
    Metabase serves. Carries the target so the caller can say which URL to use."""

    def __init__(self, location: str):
        self.location = location
        super().__init__(
            f"the Metabase instance redirects to {location or '(no target)'}: "
            "store that address as the instance URL instead.")


class MetabaseQueryError(UpstreamHTTPError):
    """The database rejected the query (Metabase answered `202` with
    `status: failed`). `error` is the database's message, `body` the full
    answer."""

    def __init__(self, body: Dict[str, Any]):
        self.error = body.get("error") or body.get("error_type") or "query failed"
        super().__init__(422, self.error, service=_SERVICE)
        self.body = body


def normalize_instance_url(instance_url: str, *, allow_http: bool = False) -> str:
    """`https://metabase.example.com` form: scheme kept (defaults to https), no
    trailing slash, no query or fragment. An instance served under a path keeps
    it; a pasted page of the app (`/dashboard/3`, `/question/12`, `/api/...`)
    is cut back to the instance root.

    `http://` is refused unless `allow_http=True`. Credentials embedded in the
    URL are refused — they would end up in the stored URL and in errors."""
    raw = (instance_url or "").strip()
    if not raw:
        raise ValueError("Metabase instance URL missing.")
    if "://" not in raw:
        raw = "https://" + raw
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("Invalid Metabase instance URL: http(s) scheme and "
                         "host name expected.")
    if parts.username is not None or parts.password is not None:
        raise ValueError("Invalid Metabase instance URL: it must not carry "
                         "credentials (user:password@). The API key is "
                         "passed on its own.")
    if parts.scheme == "http" and not allow_http:
        raise ValueError("Metabase instance URL in http:// refused: the API key "
                         "would travel in clear. Use the https:// address.")
    path = parts.path.rstrip("/")
    for segment in _APP_SEGMENTS:
        idx = (path + "/").find(segment + "/")
        if idx != -1:
            path = path[:idx]
    return f"{parts.scheme}://{parts.netloc}{path}"


def records(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """A query result's rows as dicts keyed by column name (`data.cols[].name`).
    A name repeated across columns (two joined `id`) keeps its last value."""
    data = (result or {}).get("data") or {}
    names = [c.get("name") for c in data.get("cols") or []]
    return [dict(zip(names, row)) for row in data.get("rows") or []]


def _id(name: str, value: Any) -> int:
    """A numeric id placed in a path: a strictly positive integer."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"`{name}` must be an integer > 0, got {value!r}.")
    return value


def _choices(name: str, values: Optional[Sequence[str]],
             allowed: Sequence[str]) -> Optional[List[str]]:
    if values is None:
        return None
    if isinstance(values, str):
        values = [values]
    bad = [v for v in values if v not in allowed]
    if bad:
        raise ValueError(f"`{name}`: unknown value(s) {bad}; expected one of "
                         f"{', '.join(allowed)}.")
    return list(values)


class MetabaseClient:
    """Metabase REST API, read and query only."""

    def __init__(self, instance_url: str, api_key: Optional[str] = None, *,
                 allow_http: bool = False):
        """
        Args:
            instance_url: the instance address, as users open it
                (`https://metabase.example.com`).
            api_key: an API key of the instance (`mb_...`).
            allow_http: accept an `http://` instance (local development only).
        """
        self.instance_url = normalize_instance_url(instance_url,
                                                   allow_http=allow_http)
        self.api_key = require((api_key or "").strip(), "METABASE_API_KEY")
        self.session = requests.Session()
        self.session.headers.update({"x-api-key": self.api_key,
                                     "Accept": "application/json"})

    # --- transport ----------------------------------------------------------

    def _request(self, method: str, path: str, *, params: Any = None,
                 json: Any = None) -> Any:
        if isinstance(params, dict):
            params = [(k, v) for k, v in params.items() if v is not None]
        resp = self.session.request(method, f"{self.instance_url}/api{path}",
                                    params=params or None, json=json,
                                    timeout=_HTTP_TIMEOUT, allow_redirects=False)
        if 300 <= resp.status_code < 400:
            if resp.status_code in (301, 302, 303, 307, 308):
                raise MetabaseRedirect(resp.headers.get("Location", ""))
            raise UpstreamHTTPError(502, f"unexpected HTTP {resp.status_code}",
                                    service=_SERVICE)
        raise_for_upstream(resp, service=_SERVICE)
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            raise UpstreamHTTPError(502, "the answer is not JSON — check the "
                                    "instance URL", service=_SERVICE) from None

    def _get(self, path: str, params: Any = None) -> Any:
        return self._request("GET", path, params=params)

    def _query(self, path: str, payload: Dict[str, Any]) -> Any:
        """POST a query; a `status: failed` answer is raised, not returned."""
        body = self._request("POST", path, json=payload)
        if isinstance(body, dict) and body.get("status") == "failed":
            raise MetabaseQueryError(body)
        return body

    # ================================================================
    # Identity and search
    # ================================================================

    def current_user(self) -> Any:
        """GET /api/user/current — the key's identity (an API key has a user
        of its own) and its groups. A cheap connection check."""
        return self._get("/user/current")

    def search(self, q: Optional[str] = None, *,
               models: Optional[Sequence[str]] = None,
               archived: Optional[bool] = None,
               limit: Optional[int] = None, offset: Optional[int] = None) -> Any:
        """GET /api/search — questions, models, dashboards, collections,
        tables… whose name or description matches `q`.
        `{data: [{model, id, name, collection, ...}], total, ...}`.

        Args:
            models: restrict to these kinds (`SEARCH_MODELS`), e.g.
                `["card", "dashboard"]`.
        """
        params: list = [("q", q), ("archived", _flag(archived)),
                        ("limit", limit), ("offset", offset)]
        params += [("models", m) for m in _choices("models", models,
                                                    SEARCH_MODELS) or []]
        return self._get("/search", [(k, v) for k, v in params if v is not None])

    # ================================================================
    # Collections — where questions and dashboards live
    # ================================================================

    def list_collections(self, *, archived: Optional[bool] = None) -> Any:
        """GET /api/collection — every collection the key can read (the
        personal collections of others included for an admin key)."""
        return self._get("/collection", {"archived": _flag(archived)})

    def list_collection_items(self, collection_id: Union[int, str] = "root", *,
                              models: Optional[Sequence[str]] = None) -> Any:
        """GET /api/collection/{id}/items — the questions, dashboards and
        sub-collections of one collection; `root` is the top level.
        `{data: [...], total, ...}`."""
        cid = "root" if collection_id == "root" else _id("collection_id",
                                                         collection_id)
        params = [("models", m) for m in _choices("models", models,
                                                  SEARCH_MODELS) or []]
        return self._get(f"/collection/{cid}/items", params)

    # ================================================================
    # Saved questions (cards) and dashboards
    # ================================================================

    def list_cards(self, filter: str = "all", *,
                   model_id: Optional[int] = None) -> Any:
        """GET /api/card — saved questions, models and metrics.

        Args:
            filter: one of `CARD_FILTERS`. `database` / `table` /
                `using_model` / … need `model_id` (the id of that database,
                table, model…).
        """
        _choices("filter", [filter], CARD_FILTERS)
        return self._get("/card", {"f": filter, "model_id": model_id})

    def get_card(self, card_id: int) -> Any:
        """GET /api/card/{id} — one question: its query (`dataset_query`), its
        parameters, its display and result columns."""
        return self._get(f"/card/{_id('card_id', card_id)}")

    def run_card(self, card_id: int, *,
                 parameters: Optional[Sequence[Dict[str, Any]]] = None) -> Any:
        """POST /api/card/{id}/query — run a saved question, as Metabase shows
        it. `{row_count, data: {cols, rows}, status, ...}`, 2,000 rows at most.

        Args:
            parameters: filter values, as the card declares them:
                `[{"type": "category", "target": ["variable", ["template-tag",
                "region"]], "value": ["EU"]}]`.
        """
        return self._query(f"/card/{_id('card_id', card_id)}/query",
                           {"parameters": list(parameters or [])})

    def get_dashboard(self, dashboard_id: int) -> Any:
        """GET /api/dashboard/{id} — a dashboard with its cards (`dashcards`:
        each has its own `id` and the `card_id` it shows) and its filters."""
        return self._get(f"/dashboard/{_id('dashboard_id', dashboard_id)}")

    def run_dashboard_card(self, dashboard_id: int, dashcard_id: int,
                           card_id: int, *,
                           parameters: Optional[Sequence[Dict[str, Any]]] = None
                           ) -> Any:
        """POST /api/dashboard/{id}/dashcard/{dashcard_id}/card/{card_id}/query
        — one card of a dashboard, with the dashboard's filters applied as on
        screen.

        Args:
            parameters: dashboard filter values, `[{"id": "<filter id>",
                "value": [...]}]` — the ids are in `get_dashboard`'s
                `parameters`.
        """
        path = (f"/dashboard/{_id('dashboard_id', dashboard_id)}"
                f"/dashcard/{_id('dashcard_id', dashcard_id)}"
                f"/card/{_id('card_id', card_id)}/query")
        return self._query(path, {"parameters": list(parameters or [])})

    # ================================================================
    # Schema — what can be queried
    # ================================================================

    def list_databases(self, *, include_tables: bool = False) -> Any:
        """GET /api/database — the connected databases (id, name, engine).
        `{data: [...], total}`."""
        return self._get("/database",
                         {"include": "tables" if include_tables else None})

    def get_database_metadata(self, database_id: int) -> Any:
        """GET /api/database/{id}/metadata — a database with all its tables
        and their fields (name, type, semantic type, foreign keys). Can be
        large on a wide schema; `get_table_metadata` reads one table."""
        return self._get(f"/database/{_id('database_id', database_id)}/metadata")

    def get_table_metadata(self, table_id: int) -> Any:
        """GET /api/table/{id}/query_metadata — one table, its fields and
        their foreign-key targets."""
        return self._get(f"/table/{_id('table_id', table_id)}/query_metadata")

    # ================================================================
    # Ad-hoc queries
    # ================================================================

    def run_native_query(self, database_id: int, sql: str, *,
                         template_tags: Optional[Dict[str, Any]] = None,
                         parameters: Optional[Sequence[Dict[str, Any]]] = None
                         ) -> Any:
        """POST /api/dataset — run SQL (or the database's native language) on
        one database. 2,000 rows at most: aggregate in the query.

        Args:
            sql: the statement. `{{name}}` variables need `template_tags`
                (`{"name": {"name": "name", "display-name": "Name", "type":
                "text"}}`) and their values in `parameters`.
        """
        if not (sql or "").strip():
            raise ValueError("`sql` is required.")
        native: Dict[str, Any] = {"query": sql}
        if template_tags:
            native["template-tags"] = dict(template_tags)
        payload: Dict[str, Any] = {"database": _id("database_id", database_id),
                                   "type": "native", "native": native}
        if parameters:
            payload["parameters"] = list(parameters)
        return self._query("/dataset", payload)

    def run_query(self, dataset_query: Dict[str, Any]) -> Any:
        """POST /api/dataset — run a full dataset query as Metabase stores it
        in a card's `dataset_query` (MBQL `{"database", "type": "query",
        "query": {...}}`, or native). Lets a saved question be re-run with an
        edited query."""
        if not isinstance(dataset_query, dict) or "database" not in dataset_query:
            raise ValueError("`dataset_query` must be an object with `database`.")
        return self._query("/dataset", dict(dataset_query))


def _flag(value: Optional[bool]) -> Optional[str]:
    return None if value is None else ("true" if value else "false")
