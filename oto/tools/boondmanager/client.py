"""BoondManager (Boond) API client (https://doc.boondmanager.com/api-externe/) —
the CRM of consulting firms: contacts, companies, opportunities, actions.

Deliberately small: search, read, the creation template, create. No update, no
delete, no merge — those overwrite or destroy data and stay out until they are
proven against a live instance.

## Authentication — `X-Jwt-Client-BoondManager`

A HS256 JWT signed with the **client key**, payload
`{userToken, clientToken, time, mode}`. The three secrets come from the Boond
account (administrator interface > dashboard, or user settings > security), and
REST API access must be allowed there. `time` is the UNIX time of the request:
Boond accepts a token for 5 minutes only, so one is signed per request.

A token Boond cannot verify is answered **422** (not 401), with an error whose
`source.parameter` is `xJwtClient`.

`mode` is always `"normal"`: `"god"` skips Boond's rights checks for managers,
and a connector must act with the rights of the user it was given.

## Protocol facts that shape a caller

- Bodies are JSON:API-like: `data` (`id`, `type`, `attributes`,
  `relationships`), `included`, `meta` (`totals.rows`).
- **Pagination**: `page` (from 1), `maxResults` (default 30, max 500; max 100 on
  actions). Out-of-range values are refused here: Boond silently falls back to 30.
- **`keywords` filters by id with prefixes** (`CCON12` = contact 12, `CSOC3` =
  company 3, `AO7` = opportunity 7…). A prefix an endpoint does not know returns
  0 rows instead of an error, so prefixes are checked here per entity.
- States, types, origins and action types are numeric ids described by
  `GET /application/dictionary`.
- **Quota**: Boond counts API calls per month (500 per manager on its Core plan).
  Every method is one call; nothing here pages or retries on its own beyond a
  single short-wait retry of a GET on 429.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from typing import Any, Dict, Optional

import requests

from ..common import raise_for_upstream
from ..common.credentials import require
from ._spec import (ENTITIES, KEYWORD_PREFIXES, KEYWORDS_TYPES, MAX_RESULTS,
                    PERIODS, REQUIRED_ATTRIBUTES, REQUIRED_RELATIONSHIPS,
                    RETURN_MORE_DATA, SEARCH_FILTERS, SORTS, check_attributes,
                    check_relationships)

_HTTP_TIMEOUT = (10, 60)  # (connect, read) — never an unbounded wait
BASE_URL = "https://ui.boondmanager.com/api"
JWT_HEADER = "X-Jwt-Client-BoondManager"

# The JSON:API `type` of one record of each entity.
RECORD_TYPES = {"contacts": "contact", "companies": "company",
                "opportunities": "opportunity", "actions": "action"}
ORDERS = ("asc", "desc")
_ALL_PREFIXES = frozenset(p for ps in KEYWORD_PREFIXES.values() for p in ps)

# A 429 on a GET is retried once if `Retry-After` is this short; a longer wait
# is the caller's call, not a hidden stall inside a request.
_RATE_LIMIT_MAX_WAIT = 15.0

_ID = re.compile(r"^[1-9][0-9]*$")
_PREFIXED_ID = re.compile(r"^([A-Z]+)([0-9]+)$")
_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}( [0-9]{2}:[0-9]{2}:[0-9]{2})?$")


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def sign_hs256(payload: Dict[str, Any], key: str) -> str:
    """A compact HS256 JWT of `payload` (keys kept in their given order)."""
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = ".".join(
        _b64url(json.dumps(part, separators=(",", ":")).encode("utf-8"))
        for part in (header, payload))
    signature = hmac.new(key.encode("utf-8"), signing_input.encode("ascii"),
                         hashlib.sha256).digest()
    return f"{signing_input}.{_b64url(signature)}"


def client_jwt(user_token: str, client_token: str, client_key: str,
               now: Optional[int] = None) -> str:
    """The `X-Jwt-Client-BoondManager` value: HS256 over
    `{userToken, clientToken, time, mode: "normal"}`, signed with the client key."""
    return sign_hs256({"userToken": user_token, "clientToken": client_token,
                       "time": int(time.time()) if now is None else int(now),
                       "mode": "normal"}, client_key)


def _entity(entity: str) -> str:
    if entity not in ENTITIES:
        raise ValueError(f"`entity` invalid: {entity!r}. Accepted values: "
                         + ", ".join(repr(e) for e in ENTITIES))
    return entity


def _record_id(value: Any, name: str = "id") -> str:
    """A numeric record id, refused locally when it is not one."""
    text = str(value).strip() if value is not None else ""
    if isinstance(value, bool) or not _ID.match(text):
        raise ValueError(f"`{name}` must be a numeric id; got {value!r}.")
    return text


def check_keywords(entity: str, keywords: Optional[str]) -> Optional[str]:
    """Refuse an id prefix the entity's search ignores (Boond answers 0 rows)."""
    if keywords is None:
        return None
    allowed = KEYWORD_PREFIXES[entity]
    for token in str(keywords).split():
        m = _PREFIXED_ID.match(token)
        # Only Boond's own prefixes are checked: `ISO9001` is a plain word.
        if m and m.group(1) in _ALL_PREFIXES and m.group(1) not in allowed:
            raise ValueError(
                f"`keywords`: {token!r} uses prefix {m.group(1)!r}, which {entity} "
                f"search does not understand (it would silently return 0 rows). "
                f"Accepted id prefixes: {', '.join(allowed)}.")
    return str(keywords)


def _date(value: Optional[str], name: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not _DATE.match(value.strip()):
        raise ValueError(f"`{name}` must be YYYY-MM-DD or 'YYYY-MM-DD HH:MM:SS'; "
                         f"got {value!r}.")
    return value.strip()


def _relationship(entity: str, name: str, value: Any) -> Dict[str, Any]:
    """`{"type": …, "id": …}` (a list of them, or None) as a JSON:API relationship."""
    if value is None:
        return {"data": None}

    def one(item: Any) -> Dict[str, str]:
        if not isinstance(item, dict) or "type" not in item or "id" not in item:
            raise ValueError(
                f"relationship `{name}` of a new {RECORD_TYPES[entity]} must be "
                f'{{"type": …, "id": …}}; got {item!r}.')
        return {"id": _record_id(item["id"], f"{name}.id"), "type": str(item["type"])}
    if isinstance(value, list):
        return {"data": [one(v) for v in value]}
    return {"data": one(value)}


def build_create_body(entity: str, attributes: Dict[str, Any],
                      relationships: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The JSON:API body of a creation, with the reference's required fields
    checked locally. Relationships are given as `{"company": {"type": "company",
    "id": "12"}}`."""
    _entity(entity)
    if not isinstance(attributes, dict):
        raise ValueError("`attributes` must be an object.")
    relationships = relationships or {}
    if not isinstance(relationships, dict):
        raise ValueError("`relationships` must be an object.")
    missing = [a for a in REQUIRED_ATTRIBUTES[entity]
               if attributes.get(a) in (None, "")]
    missing += [r for r in REQUIRED_RELATIONSHIPS[entity]
                if not relationships.get(r)]
    if missing:
        raise ValueError(f"a new {RECORD_TYPES[entity]} requires "
                         + ", ".join(f"`{m}`" for m in missing) + ".")
    check_attributes(entity, attributes)
    check_relationships(entity, relationships)
    data: Dict[str, Any] = {"type": RECORD_TYPES[entity], "attributes": dict(attributes)}
    if relationships:
        data["relationships"] = {k: _relationship(entity, k, v)
                                 for k, v in relationships.items()}
    return {"data": data}


class BoondManagerClient:
    """BoondManager REST API, CRM subset. Auth `X-Jwt-Client-BoondManager`."""

    BASE_URL = BASE_URL

    def __init__(self, client_token: Optional[str] = None,
                 client_key: Optional[str] = None,
                 user_token: Optional[str] = None):
        """
        Args:
            client_token: the Boond account's client token (administrator dashboard).
            client_key: the client key that signs the JWT (administrator dashboard).
            user_token: the token of the user the calls act as (user settings >
                security, or the administrator dashboard).
        """
        self.client_token = require(client_token, "BOONDMANAGER_CLIENT_TOKEN")
        self.client_key = require(client_key, "BOONDMANAGER_CLIENT_KEY")
        self.user_token = require(user_token, "BOONDMANAGER_USER_TOKEN")
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    # --- transport ----------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        # Signed per request: Boond accepts a token for 5 minutes only. Secrets
        # travel in this header only — never in the URL.
        return {JWT_HEADER: client_jwt(self.user_token, self.client_token,
                                       self.client_key)}

    @staticmethod
    def _retry_after(resp: Any) -> Optional[float]:
        raw = (getattr(resp, "headers", None) or {}).get("Retry-After")
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            return None

    def _request(self, method: str, path: str,
                 params: Optional[Dict[str, Any]] = None,
                 body: Optional[Dict[str, Any]] = None) -> Any:
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        url = f"{self.BASE_URL}{path}"

        def send():
            return self.session.request(method, url, params=clean or None,
                                        json=body, headers=self._headers(),
                                        timeout=_HTTP_TIMEOUT)

        resp = send()
        if resp.status_code == 429 and method == "GET":
            wait = self._retry_after(resp)
            if wait is not None and wait <= _RATE_LIMIT_MAX_WAIT:
                time.sleep(wait)
                resp = send()
        raise_for_upstream(resp, service="boondmanager")
        return resp.json() if resp.content else {}

    # --- application --------------------------------------------------------

    def current_user(self) -> Any:
        """GET /application/current-user — who the token acts as; authenticates
        the credential at the cost of one call."""
        return self._request("GET", "/application/current-user")

    def dictionary(self, language: Optional[str] = None) -> Any:
        """GET /application/dictionary — ids and labels of states, types, origins,
        action types… of this Boond account."""
        if language is not None and language not in ("fr", "en", "es"):
            raise ValueError(f"`language` must be fr, en or es; got {language!r}.")
        return self._request("GET", "/application/dictionary",
                             {"language": language})

    # --- CRM ----------------------------------------------------------------

    def search(self, entity: str, *, keywords: Optional[str] = None,
               keywords_type: Optional[str] = None,
               period: Optional[str] = None, start_date: Optional[str] = None,
               end_date: Optional[str] = None,
               filters: Optional[Dict[str, Any]] = None,
               sort: Optional[str] = None, order: Optional[str] = None,
               page: Optional[int] = None,
               max_results: Optional[int] = None) -> Any:
        """GET /{entity} — search contacts, companies, opportunities or actions.

        `filters` takes the entity's own list filters (`states`, `origins`,
        `flags`…) as `{name: value | [values]}`; ids come from `dictionary()`.
        """
        _entity(entity)
        params: Dict[str, Any] = {"keywords": check_keywords(entity, keywords)}
        if keywords_type is not None:
            if keywords_type not in KEYWORDS_TYPES[entity]:
                accepted = ", ".join(KEYWORDS_TYPES[entity]) or "none"
                raise ValueError(f"`keywords_type` {keywords_type!r} is not valid "
                                 f"for {entity}. Accepted values: {accepted}.")
            params["keywordsType"] = keywords_type
        if period is not None:
            if period not in PERIODS[entity]:
                raise ValueError(f"`period` {period!r} is not valid for {entity}. "
                                 f"Accepted values: {', '.join(PERIODS[entity])}.")
            if start_date is None and end_date is None:
                raise ValueError("`period` needs `start_date` and/or `end_date`.")
            params["period"] = period
        elif start_date is not None or end_date is not None:
            raise ValueError("`start_date`/`end_date` need a `period`.")
        params["startDate"] = _date(start_date, "start_date")
        params["endDate"] = _date(end_date, "end_date")
        for name, value in (filters or {}).items():
            if name not in SEARCH_FILTERS[entity]:
                raise ValueError(
                    f"filter `{name}` is not supported on {entity}. Accepted: "
                    + ", ".join(SEARCH_FILTERS[entity]) + ".")
            values = list(value) if isinstance(value, (list, tuple)) else [value]
            if not values or any(isinstance(v, bool) or not isinstance(v, (int, str))
                                 for v in values):
                raise ValueError(f"filter `{name}` takes ids (integers or "
                                 f"strings), one or a list; got {value!r}.")
            if name == "returnMoreData":
                unknown = [v for v in values if v not in RETURN_MORE_DATA[entity]]
                if unknown:
                    raise ValueError(
                        f"`returnMoreData` on {entity} accepts "
                        + ", ".join(RETURN_MORE_DATA[entity]) + f"; got {unknown}.")
            # Repeated parameters travel as `name[]=a&name[]=b`.
            params[f"{name}[]"] = values
        if sort is not None and sort not in SORTS[entity]:
            raise ValueError(f"`sort` {sort!r} is not sortable on {entity}. "
                             "Accepted: " + ", ".join(SORTS[entity]) + ".")
        if order is not None and order not in ORDERS:
            raise ValueError(f"`order` must be asc or desc; got {order!r}.")
        params.update({"sort": sort, "order": order})
        params.update(self._page(entity, page, max_results))
        return self._request("GET", f"/{entity}", params)

    @staticmethod
    def _page(entity: str, page: Optional[int],
              max_results: Optional[int]) -> Dict[str, Any]:
        if page is not None and (isinstance(page, bool) or not isinstance(page, int)
                                 or page < 1):
            raise ValueError(f"`page` must be an integer >= 1; got {page!r}.")
        cap = MAX_RESULTS[entity]
        if max_results is not None and (
                isinstance(max_results, bool) or not isinstance(max_results, int)
                or not 1 <= max_results <= cap):
            raise ValueError(f"`max_results` must be between 1 and {cap} for "
                             f"{entity} (Boond silently falls back to 30 "
                             f"otherwise); got {max_results!r}.")
        return {"page": page, "maxResults": max_results}

    def get(self, entity: str, record_id: Any) -> Any:
        """GET /{entity}/{id}/information — one record's information tab
        (`GET /actions/{id}` for an action, which has no tab)."""
        _entity(entity)
        rid = _record_id(record_id)
        if entity == "actions":
            return self._request("GET", f"/actions/{rid}")
        return self._request("GET", f"/{entity}/{rid}/information")

    def get_default(self, entity: str) -> Any:
        """GET /{entity}/default — the template Boond pre-fills for a new record
        (default state, manager, agency…)."""
        return self._request("GET", f"/{_entity(entity)}/default")

    def create(self, entity: str, attributes: Dict[str, Any],
               relationships: Optional[Dict[str, Any]] = None) -> Any:
        """POST /{entity} — create one record. Required: contact `firstName`,
        `lastName` + `company`; company `name`; opportunity `title`; action
        `typeOf` + `dependsOn`. Attributes and relationships are checked against
        the creation schema first (`_spec`). Never retried."""
        body = build_create_body(entity, attributes, relationships)
        return self._request("POST", f"/{entity}", body=body)

