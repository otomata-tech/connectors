"""Sellsy client — CRM + FR sales management (API v2, api.sellsy.com/v2).

Sellsy holds in a single account the CRM (companies, individuals, contacts,
opportunities) and the sales chain (quote → order → invoice → credit note,
payments, catalog). The v2 API is **uniform**: every resource exposes
`GET /x` (list), `POST /x/search` (filtered list), `GET|PUT|DELETE /x/{id}`,
`POST /x` (create) — hence a client built on generic verbs
(`list_records`, `search_records`, …) rather than one method per endpoint.

**Auth = OAuth2 client_credentials** (Settings → Developer portal → API V2:
a *personal* access gives a client_id + client_secret). The token is cached
**in process memory**, keyed by a hash of the id/secret pair: a multi-tenant
backend therefore cannot serve one account's token to another, and
nothing is written to disk.

**"Seek" pagination** (the one Sellsy recommends): the response carries
`pagination.offset` = opaque cursor of the next page, to be passed back as-is as
`offset`. `list_all` walks this cursor.

Quotas: Sellsy counts per second/minute/day/month and returns 429; the remainder
is readable in the `X-Quota-Remaining-By-*` headers, exposed as
`last_quota` after each call.

Docs: https://docs.sellsy.com/api/v2/

Requires: requests
"""
from __future__ import annotations

import hashlib
import re
import time
from typing import Any, Dict, List, Optional

import requests

from ..common.credentials import require
from ..common import raise_for_upstream

# Per-process shared tokens: {hash(id+secret): (token, expires_at)}. The hash
# is the key — without the plaintext secret, one cannot pick up another
# account's token (the backend serves several orgs in the same process).
_TOKEN_CACHE: Dict[str, tuple] = {}

# A resource path segment, as Sellsy names them (`credit-notes`,
# `calendar-events`). Shape guardrail: never an escape out of the path.
_RESOURCE_RE = re.compile(r"^[a-z][a-z0-9-]*(/[a-z][a-z0-9-]*)*$")


class SellsyClient:
    """Sellsy API v2 client — OAuth2 client_credentials auth."""

    AUTH_URL = "https://login.sellsy.com/oauth2/access-tokens"
    BASE_URL = "https://api.sellsy.com/v2"

    def __init__(self, client_id: Optional[str] = None,
                 client_secret: Optional[str] = None, timeout: int = 30):
        """
        Args:
            client_id: Client ID of the API v2 access.
            client_secret: Associated Client Secret.
            timeout: HTTP timeout per call, in seconds.
        """
        self.client_id = require(client_id, "SELLSY_CLIENT_ID")
        self.client_secret = require(client_secret, "SELLSY_CLIENT_SECRET")
        self.timeout = timeout
        self.session = requests.Session()
        # Remaining quotas of the LAST call (X-Quota-Remaining-By-*), useful for
        # deciding on a batch: Sellsy counts even requests that error out.
        self.last_quota: Dict[str, int] = {}

    # --- auth ---------------------------------------------------------------

    @property
    def _cache_key(self) -> str:
        digest = hashlib.sha256(
            f"{self.client_id}:{self.client_secret}".encode()).hexdigest()
        return digest

    def access_token(self) -> str:
        """Valid Bearer token — taken from the memory cache, otherwise freshly minted."""
        now = time.time()
        cached = _TOKEN_CACHE.get(self._cache_key)
        if cached and cached[1] > now + 60:
            return cached[0]

        resp = self.session.post(
            self.AUTH_URL,
            json={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            timeout=self.timeout,
        )
        raise_for_upstream(resp, service="sellsy")
        data = resp.json()
        token = data.get("access_token")
        if not token:
            raise ValueError(f"Sellsy did not return an access_token: {data}")
        _TOKEN_CACHE[self._cache_key] = (
            token, now + int(data.get("expires_in", 3600)))
        return token

    def invalidate_token(self) -> None:
        """Forget the cached token (401: it may have been revoked early)."""
        _TOKEN_CACHE.pop(self._cache_key, None)

    # --- transport ----------------------------------------------------------

    def request(self, method: str, path: str, *, params: Optional[dict] = None,
                json: Optional[dict] = None, retry_auth: bool = True) -> Any:
        """Raw call to the v2 API. Returns the parsed JSON (or `{}` on 204).

        A 401 on a cached token invalidates it and retries ONCE: the
        token can be revoked on the Sellsy side before its announced expiry.
        """
        url = f"{self.BASE_URL}/{path.lstrip('/')}"
        resp = self.session.request(
            method, url,
            params=self._encode_params(params),
            json=json,
            headers={"Authorization": f"Bearer {self.access_token()}",
                     "Accept": "application/json"},
            timeout=self.timeout,
        )
        self._read_quota(resp)
        if resp.status_code == 401 and retry_auth:
            self.invalidate_token()
            return self.request(method, path, params=params, json=json,
                                retry_auth=False)
        raise_for_upstream(resp, service="sellsy")
        return resp.json() if resp.content else {}

    def _read_quota(self, resp) -> None:
        self.last_quota = {
            unit: int(resp.headers[header])
            for unit, header in (("second", "X-Quota-Remaining-By-Second"),
                                 ("minute", "X-Quota-Remaining-By-Minute"),
                                 ("day", "X-Quota-Remaining-By-Day"),
                                 ("month", "X-Quota-Remaining-By-Month"))
            if str(resp.headers.get(header, "")).lstrip("-").isdigit()
        }

    @staticmethod
    def _encode_params(params: Optional[dict]) -> Optional[dict]:
        """Drops `None`s and passes lists in PHP style (`embed[]=company`).

        Sellsy expects `field[]=id&field[]=name`, not `field=id,name`: a Python
        list encoded without brackets would be ignored SILENTLY (all fields
        would come back, or the embed would be missing) — hence the rewrite here.
        """
        if not params:
            return None
        out: Dict[str, Any] = {}
        for key, value in params.items():
            if value is None:
                continue
            if isinstance(value, (list, tuple)):
                if not value:
                    continue
                out[f"{key}[]"] = list(value)
            elif isinstance(value, bool):
                out[key] = "true" if value else "false"
            else:
                out[key] = value
        return out

    @staticmethod
    def _resource(resource: str) -> str:
        if not _RESOURCE_RE.match(resource or ""):
            raise ValueError(f"invalid Sellsy resource name: {resource!r}")
        return resource

    def _listing_params(self, *, limit=None, offset=None, order=None,
                        direction=None, fields=None, embed=None,
                        extra: Optional[dict] = None) -> dict:
        # `field` (singular) is indeed the projection parameter name on the
        # Sellsy side — `fields` would be ignored without error.
        params = {"limit": limit, "offset": offset, "order": order,
                  "direction": direction, "field": fields, "embed": embed}
        params.update(extra or {})
        return params

    # --- generic CRUD -------------------------------------------------------

    def list_records(self, resource: str, *, limit: Optional[int] = None,
                     offset: Any = None, order: Optional[str] = None,
                     direction: Optional[str] = None,
                     fields: Optional[List[str]] = None,
                     embed: Optional[List[str]] = None,
                     extra_params: Optional[dict] = None) -> dict:
        """`GET /{resource}` — paginated list.

        Args:
            resource: Sellsy resource (`companies`, `invoices`, `credit-notes`…).
            limit: page size (max 100 on the API side, default 25).
            offset: `pagination.offset` cursor from the previous page ("seek"
                method, recommended) or an integer skip.
            order / direction: sort field and direction (`asc` | `desc`).
            fields: projection (`["id", "name"]`) — greatly shrinks the response.
            embed: related objects to include (`["company", "smart_tags"]`).

        Returns: `{pagination: {limit, count, total, offset}, data: [...]}`.
        """
        return self.request("GET", self._resource(resource),
                            params=self._listing_params(
                                limit=limit, offset=offset, order=order,
                                direction=direction, fields=fields, embed=embed,
                                extra=extra_params))

    def search_records(self, resource: str, filters: Optional[dict] = None, *,
                       limit: Optional[int] = None, offset: Any = None,
                       order: Optional[str] = None,
                       direction: Optional[str] = None,
                       fields: Optional[List[str]] = None,
                       embed: Optional[List[str]] = None) -> dict:
        """`POST /{resource}/search` — filtered list, same response shape.

        Args:
            filters: resource filters, passed as-is in
                `{"filters": {...}}` (e.g. `{"name": "acme"}`, `{"status":
                ["draft"]}`, `{"created": {"start": "...", "end": "..."}}`).
        """
        return self.request("POST", f"{self._resource(resource)}/search",
                            params=self._listing_params(
                                limit=limit, offset=offset, order=order,
                                direction=direction, fields=fields, embed=embed),
                            json={"filters": filters or {}})

    def list_all(self, resource: str, *, filters: Optional[dict] = None,
                 limit: int = 100, max_pages: int = 10,
                 fields: Optional[List[str]] = None,
                 embed: Optional[List[str]] = None,
                 extra_params: Optional[dict] = None) -> dict:
        """Walks the "seek" pagination and concatenates the pages.

        Args:
            filters: if provided, goes through `POST /search`; otherwise `GET`.
            max_pages: page ceiling (bounds the cost: each page = 1 request
                counted against the quota).

        Returns: `{data: [...], pages, truncated}` — `truncated=True` when the
            ceiling cut it off before the end.
        """
        rows: List[Any] = []
        offset: Any = None
        pages = 0
        truncated = False
        while pages < max_pages:
            if filters is not None:
                page = self.search_records(resource, filters, limit=limit,
                                           offset=offset, fields=fields,
                                           embed=embed)
            else:
                page = self.list_records(resource, limit=limit, offset=offset,
                                         fields=fields, embed=embed,
                                         extra_params=extra_params)
            chunk = page.get("data") or []
            rows.extend(chunk)
            pages += 1
            pagination = page.get("pagination") or {}
            offset = pagination.get("offset")
            if not chunk or offset is None or len(rows) >= (pagination.get("total") or 0):
                break
        else:
            truncated = True
        return {"data": rows, "pages": pages, "truncated": truncated}

    def get_record(self, resource: str, record_id: Any, *,
                   fields: Optional[List[str]] = None,
                   embed: Optional[List[str]] = None) -> dict:
        """`GET /{resource}/{id}` — the full record of an object."""
        return self.request("GET", f"{self._resource(resource)}/{record_id}",
                            params={"field": fields, "embed": embed})

    def create_record(self, resource: str, payload: dict, *,
                      embed: Optional[List[str]] = None,
                      verify: Optional[bool] = None) -> dict:
        """`POST /{resource}` — creation.

        Args:
            verify: `True` = validation only, nothing is persisted (dry run
                of a payload before actually creating).
        """
        return self.request("POST", self._resource(resource), json=payload,
                            params={"embed": embed, "verify": verify})

    def update_record(self, resource: str, record_id: Any, payload: dict, *,
                      embed: Optional[List[str]] = None) -> dict:
        """`PUT /{resource}/{id}` — update (provided fields only)."""
        return self.request("PUT", f"{self._resource(resource)}/{record_id}",
                            json=payload, params={"embed": embed})

    def patch_record(self, resource: str, record_id: Any, payload: dict) -> dict:
        """`PATCH /{resource}/{id}` — partial update (opportunities)."""
        return self.request("PATCH", f"{self._resource(resource)}/{record_id}",
                            json=payload)

    def delete_record(self, resource: str, record_id: Any) -> dict:
        """`DELETE /{resource}/{id}` — deletion."""
        return self.request("DELETE", f"{self._resource(resource)}/{record_id}")

    # --- sub-resources & actions -------------------------------------------

    def list_sub(self, resource: str, record_id: Any, sub: str, *,
                 limit: Optional[int] = None, offset: Any = None,
                 embed: Optional[List[str]] = None) -> dict:
        """`GET /{resource}/{id}/{sub}` — attached objects.

        E.g. contacts of a company, payments of an invoice, credit notes of an invoice.
        """
        return self.request(
            "GET", f"{self._resource(resource)}/{record_id}/{self._resource(sub)}",
            params={"limit": limit, "offset": offset, "embed": embed})

    def act(self, resource: str, record_id: Any, action: str, *,
            payload: Optional[dict] = None, method: str = "POST") -> dict:
        """`POST|PUT|PATCH /{resource}/{id}/{action}` — business verb.

        Covers `validate` (invoice/credit note), `status` (quote), `convert`
        (prospect → client), `step-rank` (opportunity), `payments`
        (collection on a third party).
        """
        return self.request(
            method,
            f"{self._resource(resource)}/{record_id}/{self._resource(action)}",
            json=payload)

    def get_custom_fields(self, resource: str, record_id: Any) -> dict:
        """`GET /{resource}/{id}/custom-fields` — custom fields of the record."""
        return self.request(
            "GET", f"{self._resource(resource)}/{record_id}/custom-fields")

    def set_custom_fields(self, resource: str, record_id: Any,
                          values: List[dict]) -> dict:
        """`PUT /{resource}/{id}/custom-fields` — writes custom fields.

        Args:
            values: `[{"id": <field id>, "value": <value>}, …]` — the id is read
                from `GET /custom-fields` (the account's reference list).
        """
        return self.request(
            "PUT", f"{self._resource(resource)}/{record_id}/custom-fields",
            json={"custom_fields": values})

    def link_contact_to_company(self, company_id: Any, contact_id: Any, *,
                                payload: Optional[dict] = None) -> dict:
        """`POST /companies/{id}/contacts/{contactId}` — attaches a contact."""
        return self.request("POST", f"companies/{company_id}/contacts/{contact_id}",
                            json=payload or {})

    def unlink_contact_from_company(self, company_id: Any,
                                    contact_id: Any) -> dict:
        """`DELETE /companies/{id}/contacts/{contactId}` — detaches a contact."""
        return self.request(
            "DELETE", f"companies/{company_id}/contacts/{contact_id}")

    # --- cross-object search & reference data -------------------------------

    def global_search(self, q: str, *, types: Optional[List[str]] = None,
                      limit: Optional[int] = None,
                      archived: Optional[bool] = None) -> dict:
        """`GET /search` — full-text search across all object types.

        Args:
            types: restricts to the wanted types (`company`, `company.client`,
                `individual`, `contact`, `opportunity`, `item`…).
        """
        return self.request("GET", "search",
                            params={"q": q, "type": types, "limit": limit,
                                    "archived": archived})

    def smart_tags_autocomplete(self, linked_type: str, *,
                                autocomplete: Optional[str] = None) -> dict:
        """`GET /smart-tags/{linkedtype}/autocomplete` — existing tags.

        Args:
            linked_type: type of object carrying the tag (`company`,
                `individual`, `contact`, `opportunity`, `invoice`…).
        """
        return self.request("GET",
                            f"smart-tags/{self._resource(linked_type)}/autocomplete",
                            params={"autocomplete": autocomplete})
