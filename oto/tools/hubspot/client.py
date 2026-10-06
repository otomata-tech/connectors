"""HubSpot CRM API client (v3).

Auth = **private app access token** (Bearer). Created in HubSpot:
Settings → Integrations → Private Apps → scopes `crm.objects.*` (read/write).
Passed in clear text to the constructor.

Generic surface over CRM objects: `contacts`, `companies`, `deals`,
`tickets` (and any custom object) share the same verbs
(`list/get/search/create/update/delete` + associations). Notes/engagements
have a dedicated helper because they attach to an object via an association.

Three properties of this client are worth knowing before calling it:

- **A 429 is retried, bounded; nothing else is retried.** A private app is
  allowed 190 requests / 10 s, and a CRM sync phase makes ~4 per lead:
  ~40 leads are enough to hit the ceiling. A 429 says the request was REFUSED
  (nothing was done), so replaying it has no side effects; a 5xx does not say
  whether the write went through, and replaying it would create a duplicate in a CRM. See
  `_request`.
  ⚠️ **This changes the FAILURE TIME of ALL calls of this client**, not
  just new ones: a 429 that used to return its named refusal in ≤ 30 s can
  now hold the caller for up to ~110 s before returning the SAME
  `UpstreamHTTPError(429)`. A caller with a shorter deadline than that
  will get a timeout — an anonymous refusal instead of a named one. The bound is
  on SLEEP, not on wall-clock time: the numbers are worked out above
  `RATE_LIMIT_ATTEMPTS`.
- **`batch_read_objects` returns an ENVELOPE, not a list**:
  `{"results": [...], "missing_ids": [...]}`. `missing_ids` is the only place where
  you can tell that a page shrank (HubSpot answers 207 for a vanished id, so nothing
  raises). The full contract is in its docstring — it is authoritative.
- **`batch_read_objects` is STRICT about property names**, unlike the
  single GET: a property absent from the portal makes a 400
  `PROPERTY_DOESNT_EXIST` on the ENTIRE slice, it does not come back empty. This is
  the trap the caller inherits when moving from N single reads to one
  batched read.

Docs : https://developers.hubspot.com/docs/api/crm/understanding-the-crm

Requires: requests
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Iterable, List, Optional

import requests

from ..common.credentials import require
from ..common import raise_for_upstream

# Default HubSpot association type (note → object). 202 = Note↔Contact,
# but the API accepts "HUBSPOT_DEFINED" via the /associations/default endpoint.
_NOTE_ASSOCIATION_TYPE = {
    "contacts": 202,
    "companies": 190,
    "deals": 214,
    "tickets": 216,
}

logger = logging.getLogger(__name__)

# HubSpot caps EVERY `batch/*` endpoint at 100 entries per request ("Object API
# batch endpoints are limited to 100 inputs per request"). A page of list memberships
# is worth 250: slicing is the only way to serve it.
BATCH_READ_MAX = 100

# Private app rate: 190 requests / 10 s. A CRM sync phase makes ~4
# calls per lead, so ~40 leads are enough to hit the ceiling — and an uncaught 429
# stops the run IN THE MIDDLE of a half-written record.
#
# Retrying is bounded in DURATION, not only in number of attempts: the
# handler runs in the MCP server's threadpool, which bounds CONCURRENCY and
# not time (oto-backend `docs/event-loop-perf.md`).
#
# ⚠️ WHAT IS BOUNDED, EXACTLY — and what is not:
#   • SLEEP is bounded per LOGICAL OPERATION, not per HTTP call.
#     `RATE_LIMIT_MAX_TOTAL_SLEEP` in total, whether the operation fits in one request
#     or in three slices of `batch_read_objects` (see `_RetryBudget`). That
#     bound is ABSOLUTE: it does not move with the number of ids.
#   • NETWORK WAIT TIME, on the other hand, IS NOT bounded at the operation level.
#     Each HTTP call carries its 30 s transport timeout, and an operation makes
#     as many as it has slices. HONEST worst case of a `batch_read_objects`:
#         slices × RATE_LIMIT_ATTEMPTS × 30 s  +  RATE_LIMIT_MAX_TOTAL_SLEEP
#     i.e., for 250 ids (3 slices), 9 × 30 + 20 = 290 s. A caller that has a
#     deadline sets it ITSELF: this client does not know it.
#   • raising `RATE_LIMIT_ATTEMPTS` therefore EXTENDS the worst case, by one HTTP
#     round trip (up to 30 s) per added attempt. It is not a free setting,
#     and that is the term that weighs — not the sleep, which is capped.
RATE_LIMIT_ATTEMPTS = 3            # >= 1 ; 1 = no retry. Counted per HTTP call.
RATE_LIMIT_MAX_SLEEP = 10.0        # cap on ONE wait
RATE_LIMIT_MAX_TOTAL_SLEEP = 20.0  # cap on the CUMULATIVE wait per OPERATION — the duration bound

# A 429 does not always mean "retry". HubSpot names the policy that was crossed in
# the body (`policyName`): short windows can be retried, a DAILY
# or MONTHLY quota cannot — waiting and replaying ties up a worker for
# nothing, and the refusal will come anyway.
_RATE_LIMIT_POLICY_NOT_RETRYABLE = frozenset({"DAILY", "MONTHLY"})


class _RetryBudget:
    """What a LOGICAL OPERATION is still allowed to SLEEP to ride out 429s.

    A default budget is born and dies inside a `_request`: a simple call keeps
    exactly the documented behaviour. An operation that makes N HTTP calls —
    `batch_read_objects` and its slices of 100 — builds ONE and passes it to
    each of its slices: without that the documented cap would be that of ONE request,
    and a page of 250 ids would sleep 3 × `RATE_LIMIT_MAX_TOTAL_SLEEP` while
    respecting the constant to the letter — the cap would look held without being so.

    Once exhausted, it degrades nothing: the next 429 becomes a named, immediate refusal.
    """

    __slots__ = ("sleep_left",)

    def __init__(self, sleep_left: float = RATE_LIMIT_MAX_TOTAL_SLEEP):
        self.sleep_left = float(sleep_left)


class HubSpotClient:
    """HubSpot CRM v3 client — generic CRM objects + notes + owners."""

    BASE_URL = "https://api.hubapi.com"

    def __init__(self, api_key: Optional[str] = None):
        """Initialize the client.

        Args:
            api_key: private app access token.
        """
        self.api_key = require(api_key, "HUBSPOT_API_KEY")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        })

    def _request(
        self,
        method: str,
        path: str,
        *,
        _budget: Optional["_RetryBudget"] = None,
        **kwargs,
    ) -> Any:
        """One HubSpot call, with the only retry that is safe: the 429.

        ⚠️ **A 429 is retried, 5xx are not.** A 429 says the request was
        REFUSED — nothing was done, replaying it has no side effects, whatever
        the verb. A 502/504 does not say whether the write went through: replaying a
        POST on it would create a duplicate, and a duplicate in a CRM costs more
        than the error we wanted to avoid. That difference is the whole point.

        When attempts (or the wait budget) run out we return NOTHING degraded:
        `raise_for_upstream` raises the `UpstreamHTTPError(429)` of the last try,
        `status_code` included — a named refusal that the caller can route.

        ⚠️ **What this call guarantees, and nothing more**:
        at most `RATE_LIMIT_ATTEMPTS` HTTP calls, and at most `RATE_LIMIT_MAX_TOTAL_SLEEP`
        seconds of sleep — this last cap being that of the `_budget`
        RECEIVED, hence shared with the other calls of the same logical operation if
        there are any. It is NOT a wall-clock bound: each HTTP call can
        still wait for its 30 s transport timeout. Worst case of an isolated
        `_request`: 3 × 30 s of network + 20 s of sleep ≈ 110 s before the named
        429 refusal — where, before this retry, the same refusal arrived in ≤ 30 s.
        Callers that have a deadline must set it themselves.

        Args:
            _budget: SHARED sleep budget, when several HTTP calls
                form a single operation (the slices of
                `batch_read_objects`). Omitted ⇒ a fresh budget for this call alone.
        """
        url = f"{self.BASE_URL}{path}"
        budget = _budget if _budget is not None else _RetryBudget()
        attempt = 0
        while True:
            attempt += 1
            resp = self.session.request(method, url, timeout=30, **kwargs)
            if resp.status_code != 429 or attempt >= RATE_LIMIT_ATTEMPTS:
                break
            if budget.sleep_left <= 0:
                # DURATION budget exhausted (by this call or by the previous
                # slices of the same operation): we refuse, we don't sleep —
                # and we don't replay "for free" either, that would be hammering
                # an upstream that just said no.
                break
            if not self._rate_limit_is_retryable(resp):
                break              # daily quota: waiting changes nothing
            delay = min(self._retry_delay(resp, attempt), budget.sleep_left)
            if delay > 0:
                logger.info(
                    "hubspot 429 on %s %s — retrying in %.1f s "
                    "(%d/%d, %.1f s of budget left)",
                    method, path, delay, attempt, RATE_LIMIT_ATTEMPTS,
                    budget.sleep_left - delay)
                time.sleep(delay)
                budget.sleep_left -= delay
            else:
                # `Retry-After: 0` (or negative) = "replay RIGHT NOW". This is
                # not "I have no budget left": the two states used to look alike and
                # both gave up retrying. Here we replay, without sleeping —
                # the attempt is spent, the wait is not.
                logger.info(
                    "hubspot 429 on %s %s — Retry-After zero, replaying "
                    "immediately (%d/%d)",
                    method, path, attempt, RATE_LIMIT_ATTEMPTS)
        if resp.status_code == 429:
            logger.warning(
                "hubspot 429 not recovered after %d attempt(s) on %s %s",
                attempt, method, path)
        raise_for_upstream(resp, service="hubspot")
        return resp.json() if resp.content else {}

    @staticmethod
    def _rate_limit_is_retryable(resp: Any) -> bool:
        """Can a 429 be retried?

        HubSpot names the policy that was crossed in the body (`policyName`: SECONDLY
        | TEN_SECONDLY_ROLLING | DAILY…). Unknown policy or unreadable body ⇒
        we RETRY: the frequent case is a burst.
        """
        try:
            body = resp.json()
        # noqa: SILENT — non-JSON body: the "burst" default below
        except Exception:
            return True
        policy = body.get("policyName") if isinstance(body, dict) else None
        return str(policy or "").upper() not in _RATE_LIMIT_POLICY_NOT_RETRYABLE

    @staticmethod
    def _retry_delay(resp: Any, attempt: int) -> float:
        """How long to wait after a 429: `Retry-After` if present (upstream knows
        better than we do), otherwise an exponential step.

        HubSpot does not guarantee the header, so the fallback is WRITTEN rather than
        guessed — and it is capped, because an unbounded wait in a
        handler is a freeze by another name. `Retry-After` can also be an HTTP
        date: unparseable ⇒ step.

        ⚠️ Returns 0.0 when upstream says `Retry-After: 0` (or a negative value,
        i.e. a deadline already past). 0.0 means "replay right now",
        NOT "give up": it is `_request` that tells the two apart, and
        confusing them cost the retry to a server that granted it.
        """
        raw = (getattr(resp, "headers", None) or {}).get("Retry-After")
        try:
            delay = float(raw) if raw else float(2 ** attempt)
        except (TypeError, ValueError):
            delay = float(2 ** attempt)
        return min(max(delay, 0.0), RATE_LIMIT_MAX_SLEEP)

    # --- CRM objects (generic) ---------------------------------------------

    def list_objects(
        self,
        object_type: str,
        properties: Optional[List[str]] = None,
        limit: int = 100,
        after: Optional[str] = None,
        associations: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """List the objects of a type (paginated). `after` = next-page cursor."""
        params: Dict[str, Any] = {"limit": min(limit, 100)}
        if properties:
            params["properties"] = ",".join(properties)
        if associations:
            params["associations"] = ",".join(associations)
        if after:
            params["after"] = after
        return self._request("GET", f"/crm/v3/objects/{object_type}", params=params)

    def get_object(
        self,
        object_type: str,
        object_id: str,
        properties: Optional[List[str]] = None,
        associations: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Fetch an object by id."""
        params: Dict[str, Any] = {}
        if properties:
            params["properties"] = ",".join(properties)
        if associations:
            params["associations"] = ",".join(associations)
        return self._request(
            "GET", f"/crm/v3/objects/{object_type}/{object_id}",
            params=params or None,
        )

    def search_objects(
        self,
        object_type: str,
        query: Optional[str] = None,
        filters: Optional[List[Dict[str, Any]]] = None,
        properties: Optional[List[str]] = None,
        limit: int = 100,
        after: Optional[str] = None,
        sorts: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """Search objects via the /search endpoint.

        Args:
            query: full-text search.
            filters: list of `{propertyName, operator, value}` combined with AND
                (HubSpot operators: EQ, NEQ, GT, GTE, LT, LTE, CONTAINS_TOKEN,
                HAS_PROPERTY, IN…). For `IN`, pass `values` (list).
            sorts: e.g. `[{"propertyName": "createdate", "direction": "DESCENDING"}]`.
        """
        body: Dict[str, Any] = {"limit": min(limit, 100)}
        if query:
            body["query"] = query
        if filters:
            body["filterGroups"] = [{"filters": filters}]
        if properties:
            body["properties"] = properties
        if sorts:
            body["sorts"] = sorts
        if after:
            body["after"] = after
        return self._request(
            "POST", f"/crm/v3/objects/{object_type}/search", json=body,
        )

    def create_object(
        self,
        object_type: str,
        properties: Dict[str, Any],
        associations: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Create an object. `associations` = HubSpot v3 format (list of `to`+`types`)."""
        body: Dict[str, Any] = {"properties": properties}
        if associations:
            body["associations"] = associations
        return self._request("POST", f"/crm/v3/objects/{object_type}", json=body)

    def update_object(
        self, object_type: str, object_id: str, properties: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Update (PATCH) an object's properties."""
        return self._request(
            "PATCH", f"/crm/v3/objects/{object_type}/{object_id}",
            json={"properties": properties},
        )

    def delete_object(self, object_type: str, object_id: str) -> Dict[str, Any]:
        """Archive an object (HubSpot recycle bin)."""
        return self._request("DELETE", f"/crm/v3/objects/{object_type}/{object_id}")

    def batch_read_objects(
        self,
        object_type: str,
        ids: Iterable[Any],
        properties: Optional[List[str]] = None,
        id_property: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Read N objects in one call per slice of 100, instead of N calls.

        **RETURN CONTRACT — an ENVELOPE, never a list.** This method always
        returns, and exactly, a `dict` with TWO keys, both ALWAYS
        present and never `None`:

            {
              "results":     [ <HubSpot record>, ... ],   # list[dict]
              "missing_ids": [ "<requested id>", ... ],   # list[str]
            }

        - `results`: the concatenation, in slice order, of the `results` arrays
          returned by HubSpot. Each element is the RAW HubSpot record,
          untouched (`{"id": str, "properties": {...}, "createdAt":
          …, "updatedAt": …, "archived": bool}`). ⚠️ The order is that of the HubSpot
          RESPONSES, NOT that of the requested `ids`, and HubSpot does not guarantee it:
          a caller that needs its input order indexes on `id`.
        - `missing_ids`: the REQUESTED ids (as `str`, deduplicated, in input
          order) for which no record came back. `[]` when everything came back.
        - No usable id in input ⇒ `{"results": [], "missing_ids": []}` and
          ZERO HTTP calls.
        - A slice that fails RAISES (`UpstreamHTTPError`): never a half page
          passing for the whole list, never a silent partial return.

        ⚠️ `missing_ids` is a LOCAL DIFF (what was requested minus what came
        back), not a reading of the error body: an id that is deleted, archived or
        from another portal is simply ABSENT from `results` and HubSpot answers
        207 — so `raise_for_upstream` does not raise. Without this tally, a page of
        250 members would come back as 247 rows without anyone learning of it:
        that is the "disguised success" the house refuses. A caller that throws away
        `missing_ids` reopens that hole.

        ⚠️ **Sleep bounded for the WHOLE operation, not per slice.** The slices
        share ONE `_RetryBudget`: the cumulative waits on 429 stay under
        `RATE_LIMIT_MAX_TOTAL_SLEEP` whatever the number of ids. WALL-CLOCK time,
        on the other hand, is not bounded at the operation level — it grows with the number of
        slices (each HTTP call carries its 30 s transport timeout). The
        worst-case computation is written above `RATE_LIMIT_ATTEMPTS`.

        Args:
            object_type: contacts | companies | deals | tickets | custom object.
            ids: the ids (or, with `id_property`, the values of that property).
                Deduplicated keeping input order; empty ones are dropped.
            properties: INTERNAL names of the properties to return. ⚠️ `batch/read` is
                STRICT about this, unlike the single GET: a property
                absent from the portal makes a 400 `PROPERTY_DOESNT_EXIST` on the
                whole slice, it does not come back empty. ⚠️ `[]` and `None` are
                treated the SAME — the `properties` key is omitted from the body and HubSpot
                returns its DEFAULT projection. An empty list therefore does not ask for
                "zero columns": if the caller means that, it is up to THEIR surface
                to refuse it by name.
            id_property: read by uniqueness key (`email`…) instead of the record id.
                It is then ADDED to the requested `properties` if it is not
                there — without it in the response, `missing_ids` would be incomputable
                (the `results` are keyed on the record id, not on the key). The
                comparison is exact first, then case-insensitive:
                HubSpot normalizes some keys (an email comes back lowercased),
                and flagging those rows in `missing_ids` would be false.
        """
        wanted: List[str] = list(dict.fromkeys(
            str(i) for i in ids if i is not None and str(i) != ""))
        if not wanted:
            # No id: no call at all. HubSpot would refuse an empty `inputs`,
            # and "nothing to read" is not an error.
            return {"results": [], "missing_ids": []}

        props = list(properties or [])
        if id_property and id_property not in props:
            props.append(id_property)

        # ONE sleep budget for ALL the slices. If it lived in
        # `_request`, it would start fresh on each slice: 250 ids would hit
        # three times the cap while respecting it to the letter.
        budget = _RetryBudget()

        out: List[Dict[str, Any]] = []
        for start in range(0, len(wanted), BATCH_READ_MAX):
            body: Dict[str, Any] = {
                "inputs": [{"id": i} for i in wanted[start:start + BATCH_READ_MAX]],
            }
            if props:
                body["properties"] = props
            if id_property:
                body["idProperty"] = id_property
            page = self._request(
                "POST", f"/crm/v3/objects/{object_type}/batch/read",
                json=body, _budget=budget)
            out.extend((page or {}).get("results") or [])

        seen = set()
        for record in out:
            if id_property:
                value = (record.get("properties") or {}).get(id_property)
            else:
                value = record.get("id")
            if value is not None:
                seen.add(str(value))
        folded = {v.casefold() for v in seen}
        missing = [i for i in wanted if i not in seen and i.casefold() not in folded]
        return {"results": out, "missing_ids": missing}

    # --- Associations -------------------------------------------------------

    def list_associations(
        self, object_type: str, object_id: str, to_object_type: str,
    ) -> Dict[str, Any]:
        """List the `to_object_type` objects associated with an object (e.g. a contact's deals)."""
        return self._request(
            "GET",
            f"/crm/v3/objects/{object_type}/{object_id}/associations/{to_object_type}",
        )

    # --- Notes (engagement attached to an object) ---------------------------

    def _note_association_type(self, object_type: str) -> int:
        """Note→<object> association type.

        The four standard objects are in a table (no call). For any other
        type (custom objects in particular), we ASK for the default label from
        `/crm/v4/associations/notes/<type>/labels` rather than silently falling back
        on 202 (= Note↔Contact): that default attached the note
        to the wrong association type without signalling anything.
        """
        known = _NOTE_ASSOCIATION_TYPE.get(object_type)
        if known is not None:
            return known
        labels = self._request(
            "GET", f"/crm/v4/associations/notes/{object_type}/labels")
        results = labels.get("results") or []
        for entry in results:
            if entry.get("category") == "HUBSPOT_DEFINED":
                return entry["typeId"]
        if results:
            return results[0]["typeId"]
        raise ValueError(
            f"no note→{object_type} association type: attach the note "
            "yourself via create_object('notes', ..., associations=[...])")

    def create_note(
        self, body: str, object_type: str, object_id: str,
        timestamp: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Attach a note to a CRM object.

        Args:
            body: note content (text/HTML).
            object_type: contacts | companies | deals | tickets.
            object_id: id of the object to attach the note to.
            timestamp: ISO 8601 or epoch ms (default = now on the HubSpot side).
        """
        props: Dict[str, Any] = {
            "hs_note_body": body,
            "hs_timestamp": timestamp or str(int(time.time() * 1000)),
        }
        assoc_type = self._note_association_type(object_type)
        associations = [{
            "to": {"id": object_id},
            "types": [{
                "associationCategory": "HUBSPOT_DEFINED",
                "associationTypeId": assoc_type,
            }],
        }]
        return self.create_object("notes", props, associations=associations)

    # --- Owners -------------------------------------------------------------

    def list_owners(self, limit: int = 100, after: Optional[str] = None) -> Dict[str, Any]:
        """List owners (HubSpot users) — for assigning contacts/deals."""
        params: Dict[str, Any] = {"limit": min(limit, 100)}
        if after:
            params["after"] = after
        return self._request("GET", "/crm/v3/owners", params=params)

    # --- Properties (schema) ------------------------------------------------
    # Without this reference, every create/update is guesswork: internal names
    # are NOT the UI labels (`dealstage`, not "Deal stage")
    # and dropdowns only accept their `options[].value`. It is
    # also what makes it possible to write a dynamic list's `filterBranch`, which
    # references properties by internal name.

    def list_properties(
        self, object_type: str, archived: bool = False,
        properties: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """List the properties of an object type (internal name, type, options)."""
        params: Dict[str, Any] = {"archived": str(archived).lower()}
        if properties:
            params["properties"] = ",".join(properties)
        return self._request(
            "GET", f"/crm/v3/properties/{object_type}", params=params)

    def get_property(
        self, object_type: str, property_name: str, archived: bool = False,
    ) -> Dict[str, Any]:
        """Fetch ONE property by its internal name."""
        return self._request(
            "GET", f"/crm/v3/properties/{object_type}/{property_name}",
            params={"archived": str(archived).lower()})

    def create_property(
        self, object_type: str, definition: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Create a property. `definition` = {name, label, type, fieldType,
        groupName, options?} (see the HubSpot Properties docs)."""
        return self._request(
            "POST", f"/crm/v3/properties/{object_type}", json=definition)

    def update_property(
        self, object_type: str, property_name: str, definition: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Update (PATCH) a property."""
        return self._request(
            "PATCH", f"/crm/v3/properties/{object_type}/{property_name}",
            json=definition)

    def delete_property(self, object_type: str, property_name: str) -> Dict[str, Any]:
        """Archive a property."""
        return self._request(
            "DELETE", f"/crm/v3/properties/{object_type}/{property_name}")

    def list_property_groups(self, object_type: str) -> Dict[str, Any]:
        """List the property groups (record tabs) of an object type."""
        return self._request("GET", f"/crm/v3/properties/{object_type}/groups")

    # --- Lists (= HubSpot "segments") ---------------------------------------
    # `/crm/v3/lists`; the v1 API (`/contacts/v1/lists`) has been sunset since
    # 2026-04-30, do not fall back to it. Lists are keyed on a NUMERIC
    # `objectTypeId` (`0-1` contacts, `0-2` companies, `0-3` deals,
    # `0-5` tickets, `2-<n>` custom objects) and not on the object name used
    # everywhere else in this client — translation is done on the caller side.
    #
    # `processingType`:
    #   MANUAL   → members managed by hand / by the API (the memberships endpoints)
    #   DYNAMIC  → members recomputed by HubSpot from `filterBranch`; the write
    #              memberships endpoints are REFUSED on it
    #   SNAPSHOT → filtered once then frozen, members managed by hand afterwards

    def create_list(
        self,
        name: str,
        object_type_id: str,
        processing_type: str = "MANUAL",
        filter_branch: Optional[Dict[str, Any]] = None,
        custom_properties: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a list.

        Args:
            name: list name (unique per object type).
            object_type_id: `0-1` (contacts), `0-2`, `0-3`, `0-5`, `2-<n>`…
            processing_type: MANUAL | DYNAMIC | SNAPSHOT.
            filter_branch: criteria tree (DYNAMIC/SNAPSHOT). Passed as is:
                it is a recursive HubSpot structure (filterBranchType
                OR/AND/UNIFIED_EVENTS/ASSOCIATION), not modelled here.
        """
        body: Dict[str, Any] = {
            "name": name,
            "objectTypeId": object_type_id,
            "processingType": processing_type,
        }
        if filter_branch is not None:
            body["filterBranch"] = filter_branch
        if custom_properties:
            body["customProperties"] = custom_properties
        return self._request("POST", "/crm/v3/lists", json=body)

    def get_list(self, list_id: str, include_filters: bool = False) -> Dict[str, Any]:
        """Fetch a list by id. `include_filters` returns its `filterBranch`."""
        return self._request(
            "GET", f"/crm/v3/lists/{list_id}",
            params={"includeFilters": str(include_filters).lower()})

    def get_lists(
        self, list_ids: List[str], include_filters: bool = False,
    ) -> Dict[str, Any]:
        """Fetch several lists in one call (repeated `listIds`)."""
        return self._request(
            "GET", "/crm/v3/lists",
            params={
                "listIds": list_ids,
                "includeFilters": str(include_filters).lower(),
            })

    def get_list_by_name(
        self, object_type_id: str, list_name: str, include_filters: bool = False,
    ) -> Dict[str, Any]:
        """Fetch a list by its name (within a given object type)."""
        return self._request(
            "GET",
            f"/crm/v3/lists/object-type-id/{object_type_id}/name/{list_name}",
            params={"includeFilters": str(include_filters).lower()})

    def search_lists(
        self,
        query: Optional[str] = None,
        processing_types: Optional[List[str]] = None,
        object_type_id: Optional[str] = None,
        count: Optional[int] = None,
        offset: Optional[int] = None,
        additional_properties: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Search lists by name / processing type / object type."""
        body: Dict[str, Any] = {}
        if query:
            body["query"] = query
        if processing_types:
            body["processingTypes"] = processing_types
        if object_type_id:
            body["objectTypeId"] = object_type_id
        if count is not None:
            body["count"] = count
        if offset is not None:
            body["offset"] = offset
        if additional_properties:
            body["additionalProperties"] = additional_properties
        return self._request("POST", "/crm/v3/lists/search", json=body)

    def update_list_name(
        self, list_id: str, list_name: str, include_filters: bool = False,
    ) -> Dict[str, Any]:
        """Rename a list (the name goes in a query param, not in the body)."""
        return self._request(
            "PUT", f"/crm/v3/lists/{list_id}/update-list-name",
            params={
                "listName": list_name,
                "includeFilters": str(include_filters).lower(),
            })

    def update_list_filters(
        self,
        list_id: str,
        filter_branch: Dict[str, Any],
        enroll_objects_in_workflows: bool = False,
    ) -> Dict[str, Any]:
        """Replace the criteria tree of a DYNAMIC/SNAPSHOT list."""
        return self._request(
            "PUT", f"/crm/v3/lists/{list_id}/update-list-filters",
            params={
                "enrollObjectsInWorkflows": str(enroll_objects_in_workflows).lower(),
            },
            json={"filterBranch": filter_branch})

    def delete_list(self, list_id: str) -> Dict[str, Any]:
        """Delete a list — restorable for 90 days (`restore_list`)."""
        return self._request("DELETE", f"/crm/v3/lists/{list_id}")

    def restore_list(self, list_id: str) -> Dict[str, Any]:
        """Restore a deleted list (90-day window)."""
        return self._request("PUT", f"/crm/v3/lists/{list_id}/restore")

    # --- Memberships (members of a list) ------------------------------------

    def get_list_memberships(
        self, list_id: str, limit: int = 100, after: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List the ids of the records that are members of a list (paginated)."""
        params: Dict[str, Any] = {"limit": min(limit, 250)}
        if after:
            params["after"] = after
        return self._request(
            "GET", f"/crm/v3/lists/{list_id}/memberships", params=params)

    def add_list_memberships(
        self, list_id: str, record_ids: List[str],
    ) -> Dict[str, Any]:
        """Add records to a MANUAL/SNAPSHOT list.

        ⚠️ The body is a BARE ARRAY of ids (`["1","2"]`), not an object.
        """
        return self._request(
            "PUT", f"/crm/v3/lists/{list_id}/memberships/add", json=record_ids)

    def remove_list_memberships(
        self, list_id: str, record_ids: List[str],
    ) -> Dict[str, Any]:
        """Remove records from a list (body = bare array of ids)."""
        return self._request(
            "PUT", f"/crm/v3/lists/{list_id}/memberships/remove", json=record_ids)

    def add_and_remove_list_memberships(
        self,
        list_id: str,
        record_ids_to_add: Optional[List[str]] = None,
        record_ids_to_remove: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Add AND remove in a single operation (a single list revision)."""
        return self._request(
            "PUT", f"/crm/v3/lists/{list_id}/memberships/add-and-remove",
            json={
                "recordIdsToAdd": record_ids_to_add or [],
                "recordIdsToRemove": record_ids_to_remove or [],
            })

    def delete_all_list_memberships(self, list_id: str) -> Dict[str, Any]:
        """Empty a list of ALL its members (the list itself survives)."""
        return self._request("DELETE", f"/crm/v3/lists/{list_id}/memberships")

    def add_memberships_from_list(
        self, list_id: str, source_list_id: str,
    ) -> Dict[str, Any]:
        """Copy the members of another list (HubSpot cap: 100,000)."""
        return self._request(
            "PUT",
            f"/crm/v3/lists/{list_id}/memberships/add-from/{source_list_id}")

    def get_record_memberships(
        self, object_type_id: str, record_id: str,
    ) -> Dict[str, Any]:
        """Lists that ONE record belongs to."""
        return self._request(
            "GET",
            f"/crm/v3/lists/records/{object_type_id}/{record_id}/memberships")
