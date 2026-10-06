"""LinkedIn search (classic / sales navigator / recruiter) and facets.

Extracted from `client.py` (split by domain, frozen public surface):
the bodies are unchanged. This mixin is never instantiated on its own — it is
composed into `UnipileClient`, which provides the transport (`_request`,
`_acct`, `_norm`, `_by_shape`, `session`).
"""

from __future__ import annotations

from typing import Any, Optional

from ..const import _API_PREFIX, _SCRAPE_TIMEOUT, _URL_SEARCH_TIMEOUT
from ..errors import UnipileError


class _SearchMixin:
    """LinkedIn search (classic / sales navigator / recruiter) and facets."""

    def resolve_facet(
        self, facet_type: str, keywords: str, limit: int = 100
    ) -> list[dict]:
        """Resolve a name into LinkedIn facet candidates (v2:
        `GET /v2/{account}/linkedin/search/parameters`). Returns `[{id, name}]` —
        the `name` is the readable LABEL ("Microsoft Excel"), essential for
        an agent to DISAMBIGUATE (e.g. 6 candidates for "Microsoft Excel"): the
        response carries the label under `name` (not `title`, historically null)."""
        params = {"type": facet_type, "keywords": keywords, "limit": limit}
        data = self._request(
            "GET", self._acct("/linkedin/search/parameters"), params=params
        )
        items = (data or {}).get("data") or (data or {}).get("items") or []
        return [{"id": it.get("id"),
                 "name": it.get("name") or it.get("title")} for it in items]

    def _as_facet_ids(self, facet_type: str, values: Optional[list[str]]) -> list[str]:
        if not values:
            return []
        out: list[str] = []
        for v in values:
            v = str(v).strip()
            if v.isdigit():
                out.append(v)
                continue
            matches = self.resolve_facet(facet_type, v)
            if not matches:
                raise UnipileError(f"Facet {facet_type} not found for: {v!r}")
            out.append(str(matches[0]["id"]))
        return out

    # ---- search ----------------------------------------------------------

    def search(
        self,
        keywords: Optional[str] = None,
        category: str = "people",
        company: Optional[list[str]] = None,
        location: Optional[list[str]] = None,
        cursor: Optional[str] = None,
        api: str = "classic",
        network_distance: Optional[list[int]] = None,
        url: Optional[str] = None,
        advanced_keywords: Optional[dict] = None,
        industry: Optional[dict] = None,
        skills: Optional[list] = None,
    ) -> dict:
        """LinkedIn search. `company`/`location`/`industry`/`skills` = names (resolved
        into facets) or numeric ids; `industry`/`skills` also accept a dict
        `{include?, exclude?}`. The encoding shapes vary by PRODUCT and by
        FACET (verified live, see `_facet_field`) — the caller just passes names/ids."""
        prefix = _API_PREFIX.get(api, _API_PREFIX["classic"])
        # #238: CURSOR-ONLY pagination. The cursor ALREADY encodes the whole request
        # (keywords + facets). We do NOT rebuild the body and do NOT re-resolve
        # the facets (each name→id = an upstream GET; stacked, they made the Recruiter
        # pages time out at 180s). We just send the cursor to the product's
        # structured endpoint. (A search by `url` doesn't produce a
        # cursor → all pagination is structured.)
        if cursor:
            cat = "companies" if category == "companies" else "people"
            return self._norm(self._request(
                "POST", self._acct(f"{prefix}/{cat}"),
                params={"cursor": cursor}, json={}, timeout=_SCRAPE_TIMEOUT))
        params: dict[str, Any] = {}

        # Search by pasted URL: the product's from-url endpoint, body {url}.
        if url:
            try:
                return self._norm(self._request(
                    "POST", self._acct(prefix), params=params, json={"url": url},
                    timeout=_URL_SEARCH_TIMEOUT))
            except UnipileError as e:
                # Network/timeout WITHOUT HTTP status = the from-url endpoint never
                # answered → most likely an expired/dead searchContextId (#238).
                # CLEAN, actionable error instead of an opaque MCP timeout.
                if getattr(e, "status_code", None) is None:
                    raise UnipileError(
                        "Recruiter search by URL unreachable — the search "
                        "context (the URL's searchContextId) has probably expired "
                        "on the LinkedIn side. Regenerate the URL from your Recruiter history, "
                        "or switch to the STRUCTURED search (api='recruiter' + "
                        "keywords/company/location) then paginate by cursor.") from e
                raise

        cat = "companies" if category == "companies" else "people"
        api_norm = api if api in _API_PREFIX else "classic"
        path = f"{prefix}/{cat}"
        body: dict[str, Any] = {}
        if keywords:
            body["keywords"] = keywords
        if advanced_keywords:
            ak = {k: v for k, v in advanced_keywords.items() if v}
            if ak:
                body["advanced_keywords"] = ak
        # ⚠️ The SHAPE of the facets (location/company/industry) differs per product
        # (v2 API contract verified live) — see `_facet_field`:
        #   classic          : flat list of ids ["123"] (inclusion only)
        #   sales_navigator  : {include:[ids], exclude:[ids]}
        #   recruiter        : [{id, ...}] (objects)
        loc = self._facet_field("LOCATION", location, api_norm)
        if loc is not None:
            body["location"] = loc
        ind = self._facet_field(
            "INDUSTRY", industry, api_norm,
            dict_input=True,  # `industry` is a dict {include?, exclude?}
        )
        if ind is not None:
            body["industry"] = ind
        comp = self._facet_field("COMPANY", company, api_norm)
        if comp is not None:
            # people-search: current EMPLOYER filter (`current_company`);
            # companies-search: the company filter doesn't exist (we omit it).
            if cat == "people":
                body["current_company"] = comp
        if cat == "people" and network_distance:
            body["network_distance"] = [int(d) for d in network_distance]
        if cat == "people":
            # `skills` = SAME per-product facet encoding as location/industry
            # (`_facet_field`): recruiter → `[{id}]` (implicit MUST_HAVE) and
            # `[{id, priority:"DOESNT_HAVE"}]` for exclusion — shape confirmed by the
            # Unipile doc (Recruiter people search). Accepts names/ids OR dict
            # `{include?, exclude?}` (like industry).
            sk = self._facet_field("SKILL", skills, api_norm,
                                   dict_input=isinstance(skills, dict))
            if sk is not None:
                body["skills"] = sk
        return self._norm(self._request(
            "POST", self._acct(path), params=params, json=body,
            timeout=_SCRAPE_TIMEOUT,
        ))

    def _facet_field(self, facet_type: str, value, api: str,
                     dict_input: bool = False):
        """Encode a facet filter according to the PRODUCT (v2 contract verified live).

        `value` = list of names/ids (default) OR dict `{include?, exclude?}` of
        names/ids (`dict_input=True`, for `industry`). Returns the value ready for
        the body, or None if nothing. `exclude` on `classic` RAISES (the classic API has
        no exclusion — concatenating include+exclude returned the EXCLUDED ones, wrong
        silently)."""
        if dict_input:
            inc = self._as_facet_ids(facet_type, (value or {}).get("include"))
            exc = self._as_facet_ids(facet_type, (value or {}).get("exclude"))
        else:
            inc = self._as_facet_ids(facet_type, value)
            exc = []
        if not inc and not exc:
            return None
        if api == "classic":
            if exc:
                raise UnipileError(
                    f"exclusion not supported by api='classic' for {facet_type.lower()}: "
                    "the LinkedIn classic API only accepts an INCLUDE list. Remove "
                    "`exclude`, or use api='sales_navigator' / 'recruiter'.")
            return inc  # flat list of ids
        if api == "sales_navigator":
            out: dict[str, Any] = {}
            if inc:
                out["include"] = inc
            if exc:
                out["exclude"] = exc
            return out
        # recruiter: the shape depends on the FACET (verified LIVE, selected contract):
        #   INDUSTRY → object `{include:[ids], exclude:[ids]}` (like sales_navigator);
        #   SKILL    → `[{name: <id>}]` — ⚠️ the field is called `name` but carries the ID
        #              (a `name`=label does NOT filter; `{id,...}` raises 400);
        #   LOCATION/COMPANY (default) → `[{id}]`.
        # Exclusion everywhere via `priority: "DOESNT_HAVE"`.
        if facet_type == "INDUSTRY":
            out2: dict[str, Any] = {}
            if inc:
                out2["include"] = inc
            if exc:
                out2["exclude"] = exc
            return out2
        key = "name" if facet_type == "SKILL" else "id"
        objs = [{key: i} for i in inc]
        objs += [{key: i, "priority": "DOESNT_HAVE"} for i in exc]
        return objs
