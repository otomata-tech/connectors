"""Pipedrive CRM API client.

Auth = **personal API token** (Settings → Personal preferences → API), passed in
the `x-api-token` header. ⚠️ NEVER in the `?api_token=` query string (the token would end up
in the URL, hence in exception messages and access logs — see CLAUDE.md).

Two API versions coexist at Pipedrive and we keep that fact visible:
- **v2** (`/api/v2`) = the current CRM: deals, persons, organizations, activities,
  products, pipelines, stages + the search endpoints. **Cursor** pagination
  (`cursor`/`limit`), custom fields grouped under `custom_fields`.
- **v1** = what has not (yet) been ported: notes, users, leads (CRUD).
  **Offset** pagination (`start`/`limit`).

Base URL: `https://api.pipedrive.com` by default (server declared by the official
OpenAPI spec). Passing `company_domain` (the account subdomain, e.g.
`acme` for `acme.pipedrive.com`) routes the request to the right data center —
recommended by Pipedrive for latency, not required for auth.

Docs: https://developers.pipedrive.com/docs/api/v1

Requires: requests
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

from ..common.credentials import require
from ..common import FieldFilter, raise_for_upstream

# Entities served by the v2 API with generic CRUD (path = the name itself).
V2_ENTITIES = ("deals", "persons", "organizations", "activities", "products",
               "pipelines", "stages")

# Entities that have a `/{entity}/search` endpoint in v2. `leads` has ONLY
# search in v2 (its CRUD stayed in v1).
SEARCHABLE = ("deals", "persons", "organizations", "products", "leads")

# "Fields" endpoint (schema, custom fields included) per entity.
_FIELDS_ENDPOINT = {
    "deals": "dealFields",
    "persons": "personFields",
    "organizations": "organizationFields",
    "products": "productFields",
    "activities": "activityFields",
}


class PipedriveClient:
    """Pipedrive client — generic v2 CRUD + notes/users/leads that stayed in v1."""

    def __init__(
        self,
        api_token: Optional[str] = None,
        company_domain: Optional[str] = None,
        field_filter: Optional[FieldFilter] = None,
    ):
        """Initialize the client.

        Args:
            api_token: personal API token.
            company_domain: account subdomain (`acme` for acme.pipedrive.com).
                Optional — routes to the right data center.
            field_filter: field redaction (default = `pipedrive` policy).
        """
        self.api_token = require(api_token, "PIPEDRIVE_API_TOKEN")
        self.company_domain = (company_domain or "").strip().strip(".") or None
        self.field_filter = field_filter or FieldFilter.from_config("pipedrive")
        self.session = requests.Session()
        self.session.headers.update({
            "x-api-token": self.api_token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    # --- transport ----------------------------------------------------------

    def _url(self, version: str, path: str) -> str:
        """Absolute URL. The v1 prefix differs by host (`/v1` on
        api.pipedrive.com, `/api/v1` on the account domain)."""
        if self.company_domain:
            return f"https://{self.company_domain}.pipedrive.com/api/{version}{path}"
        prefix = "/api/v2" if version == "v2" else "/v1"
        return f"https://api.pipedrive.com{prefix}{path}"

    def _request(self, method: str, version: str, path: str, **kwargs) -> Any:
        resp = self.session.request(
            method, self._url(version, path), timeout=30, **kwargs)
        raise_for_upstream(resp, service="pipedrive")
        if not resp.content:
            return {}
        return self.field_filter.apply(resp.json())

    @staticmethod
    def _clean(params: Dict[str, Any]) -> Dict[str, Any]:
        """Drop unset parameters (the API rejects an explicit `null`)."""
        return {k: v for k, v in params.items() if v is not None}

    @staticmethod
    def _collection(payload: Any) -> Dict[str, Any]:
        """Normalize the list envelope: `{data, next_cursor}`.

        `success: true` is noise for the agent (failure already raises); the
        cursor, for its part, is buried in `additional_data` — we surface it.
        """
        if not isinstance(payload, dict):
            return {"data": payload, "next_cursor": None}
        extra = payload.get("additional_data") or {}
        cursor = extra.get("next_cursor")
        if cursor is None:  # v1: offset pagination
            pagination = extra.get("pagination") or {}
            more = pagination.get("more_items_in_collection")
            cursor = pagination.get("next_start") if more else None
        return {"data": payload.get("data"), "next_cursor": cursor}

    @staticmethod
    def _check_entity(entity: str, allowed: tuple) -> str:
        if entity not in allowed:
            raise ValueError(
                f"unknown Pipedrive entity: {entity!r} — expected {', '.join(allowed)}")
        return entity

    # --- Generic CRUD (v2 API) ----------------------------------------------

    def list_records(
        self,
        entity: str,
        limit: int = 100,
        cursor: Optional[str] = None,
        **filters,
    ) -> Dict[str, Any]:
        """List an entity's records (cursor-paginated).

        Args:
            entity: deals | persons | organizations | activities | products |
                pipelines | stages.
            limit: 1..500 (API default: 100).
            cursor: `next_cursor` returned by the previous call.
            **filters: endpoint filters (owner_id, org_id, person_id,
                pipeline_id, stage_id, status, filter_id, updated_since,
                sort_by, sort_direction, include_fields, custom_fields…).
        """
        self._check_entity(entity, V2_ENTITIES)
        params = self._clean({"limit": min(limit, 500), "cursor": cursor, **filters})
        return self._collection(self._request("GET", "v2", f"/{entity}", params=params))

    def get_record(
        self,
        entity: str,
        record_id: int,
        include_fields: Optional[str] = None,
        custom_fields: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch a record by id."""
        self._check_entity(entity, V2_ENTITIES)
        params = self._clean(
            {"include_fields": include_fields, "custom_fields": custom_fields})
        return self._request("GET", "v2", f"/{entity}/{record_id}", params=params)

    def create_record(self, entity: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a record (`data` = JSON body of the v2 API)."""
        self._check_entity(entity, V2_ENTITIES)
        return self._request("POST", "v2", f"/{entity}", json=data)

    def update_record(
        self, entity: str, record_id: int, data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Update a record (partial PATCH — v2 no longer uses PUT)."""
        self._check_entity(entity, V2_ENTITIES)
        return self._request("PATCH", "v2", f"/{entity}/{record_id}", json=data)

    def delete_record(self, entity: str, record_id: int) -> Dict[str, Any]:
        """Delete a record."""
        self._check_entity(entity, V2_ENTITIES)
        return self._request("DELETE", "v2", f"/{entity}/{record_id}")

    # --- Search -------------------------------------------------------------

    def search(
        self,
        entity: str,
        term: str,
        fields: Optional[str] = None,
        exact_match: bool = False,
        limit: int = 100,
        cursor: Optional[str] = None,
        **filters,
    ) -> Dict[str, Any]:
        """Full-text search within ONE entity.

        Args:
            entity: deals | persons | organizations | products | leads.
            term: ≥2 characters (just 1 if `exact_match`).
            fields: fields searched, comma-separated (default = all
                searchable fields; e.g. `name,email` on persons).
            exact_match: exact match (case-insensitive).
            **filters: person_id / organization_id / status depending on the entity.
        """
        self._check_entity(entity, SEARCHABLE)
        params = self._clean({
            "term": term, "fields": fields, "limit": min(limit, 500),
            "cursor": cursor, **filters,
        })
        if exact_match:
            params["exact_match"] = "true"
        return self._collection(
            self._request("GET", "v2", f"/{entity}/search", params=params))

    def search_all(
        self,
        term: str,
        item_types: Optional[str] = None,
        fields: Optional[str] = None,
        exact_match: bool = False,
        search_for_related_items: bool = False,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Cross-entity search (`/itemSearch`) over several object types.

        Args:
            item_types: types searched, comma-separated — deal, person,
                organization, product, lead, file, mail_attachment, project.
            search_for_related_items: also attaches the objects related to the results.
        """
        params = self._clean({
            "term": term, "item_types": item_types, "fields": fields,
            "limit": min(limit, 500), "cursor": cursor,
        })
        if exact_match:
            params["exact_match"] = "true"
        if search_for_related_items:
            params["search_for_related_items"] = "true"
        return self._collection(self._request("GET", "v2", "/itemSearch", params=params))

    # --- Schema (fields, custom fields) -------------------------------------

    def list_fields(
        self, entity: str, limit: int = 100, cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List an entity's fields — **key to custom fields**: they are
        keyed by a 40-character hash, which this call gives (with their
        label, type and options).

        Args:
            entity: deals | persons | organizations | products | activities.
        """
        endpoint = _FIELDS_ENDPOINT.get(entity)
        if not endpoint:
            raise ValueError(
                f"no fields endpoint for {entity!r} — expected "
                f"{', '.join(_FIELDS_ENDPOINT)}")
        params = self._clean({"limit": min(limit, 500), "cursor": cursor})
        return self._collection(
            self._request("GET", "v2", f"/{endpoint}", params=params))

    # --- Notes (API v1) -----------------------------------------------------

    def list_notes(
        self,
        deal_id: Optional[int] = None,
        person_id: Optional[int] = None,
        org_id: Optional[int] = None,
        lead_id: Optional[str] = None,
        user_id: Optional[int] = None,
        limit: int = 100,
        start: int = 0,
        sort: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List notes, filtered by linked object (offset pagination `start`)."""
        params = self._clean({
            "deal_id": deal_id, "person_id": person_id, "org_id": org_id,
            "lead_id": lead_id, "user_id": user_id, "limit": limit,
            "start": start, "sort": sort,
        })
        return self._collection(self._request("GET", "v1", "/notes", params=params))

    def create_note(
        self,
        content: str,
        deal_id: Optional[int] = None,
        person_id: Optional[int] = None,
        org_id: Optional[int] = None,
        lead_id: Optional[str] = None,
        **extra,
    ) -> Dict[str, Any]:
        """Attach a note to a deal / person / organization / lead.

        Args:
            content: note body (HTML accepted).
            **extra: additional v1 fields (pinned_to_deal_flag, add_time…).
        """
        if not any([deal_id, person_id, org_id, lead_id]):
            raise ValueError(
                "a note must target an object: deal_id, person_id, org_id or lead_id")
        body = self._clean({
            "content": content, "deal_id": deal_id, "person_id": person_id,
            "org_id": org_id, "lead_id": lead_id, **extra,
        })
        return self._request("POST", "v1", "/notes", json=body)

    def update_note(self, note_id: int, content: str, **extra) -> Dict[str, Any]:
        """Update a note's content."""
        return self._request(
            "PUT", "v1", f"/notes/{note_id}", json={"content": content, **extra})

    def delete_note(self, note_id: int) -> Dict[str, Any]:
        """Delete a note."""
        return self._request("DELETE", "v1", f"/notes/{note_id}")

    # --- Leads (CRUD stayed in v1; search is in v2 via `search`) -------------

    def list_leads(
        self,
        owner_id: Optional[int] = None,
        person_id: Optional[int] = None,
        organization_id: Optional[int] = None,
        filter_id: Optional[int] = None,
        limit: int = 100,
        start: int = 0,
        archived_status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List leads (Pipedrive's Leads Inbox).

        Args:
            archived_status: archived | not_archived | all (API default: all).
        """
        params = self._clean({
            "owner_id": owner_id, "person_id": person_id,
            "organization_id": organization_id, "filter_id": filter_id,
            "limit": limit, "start": start, "archived_status": archived_status,
        })
        return self._collection(self._request("GET", "v1", "/leads", params=params))

    def create_lead(
        self,
        title: str,
        person_id: Optional[int] = None,
        organization_id: Optional[int] = None,
        owner_id: Optional[int] = None,
        value: Optional[Dict[str, Any]] = None,
        expected_close_date: Optional[str] = None,
        **extra,
    ) -> Dict[str, Any]:
        """Create a lead. It must be linked to a person OR an organization.

        Args:
            value: `{"amount": 1000, "currency": "EUR"}`.
        """
        if not (person_id or organization_id):
            raise ValueError("a lead requires person_id or organization_id")
        body = self._clean({
            "title": title, "person_id": person_id,
            "organization_id": organization_id, "owner_id": owner_id,
            "value": value, "expected_close_date": expected_close_date, **extra,
        })
        return self._request("POST", "v1", "/leads", json=body)

    # --- Users (v1 API) -----------------------------------------------------

    def list_users(self) -> Dict[str, Any]:
        """List the account's users — to assign an `owner_id`."""
        return self._collection(self._request("GET", "v1", "/users"))

    def get_current_user(self) -> Dict[str, Any]:
        """The user holding the token (account, company, currency) — serves as a probe."""
        return self._request("GET", "v1", "/users/me")

    # --- Pipelines / stages (readable shortcuts over `list_records`) --------

    def list_pipelines(self, limit: int = 100) -> List[Dict[str, Any]]:
        """The account's pipelines (id → name), to locate a deal."""
        return self.list_records("pipelines", limit=limit).get("data") or []

    def list_stages(
        self, pipeline_id: Optional[int] = None, limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Stages, optionally of a single pipeline."""
        params = {"pipeline_id": pipeline_id} if pipeline_id else {}
        return self.list_records("stages", limit=limit, **params).get("data") or []
