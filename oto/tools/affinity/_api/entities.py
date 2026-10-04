"""Affinity persons, companies and opportunities: search (v1), reads (v2), writes
(v1 for core attributes, v2 for global field values), relationship strength.

Composed into `AffinityClient`, which provides the transport (`_v1`, `_v2`,
`_fields`, `_plural`). Contract and protocol facts: `../client.py`.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..values import ENTITY_KINDS, _choice, _id, _ids, _limit


class _EntitiesMixin:
    # --- persons & companies: search (v1, stable) ----------------------------

    def search_persons(self, term: Optional[str] = None, *,
                       with_interaction_dates: Optional[bool] = None,
                       page_size: Optional[int] = None,
                       page_token: Optional[str] = None) -> Any:
        """GET /persons — persons whose name or email contains `term`.

        With `with_interaction_dates`, persons without any interaction are left
        out of the answer (v1 behaviour).
        """
        return self._v1("GET", "/persons", params={
            "term": term or None,
            "with_interaction_dates": "true" if with_interaction_dates else None,
            "page_size": page_size, "page_token": page_token})

    def search_organizations(self, term: Optional[str] = None, *,
                             with_interaction_dates: Optional[bool] = None,
                             page_size: Optional[int] = None,
                             page_token: Optional[str] = None) -> Any:
        """GET /organizations — organizations whose name or domain contains `term`."""
        return self._v1("GET", "/organizations", params={
            "term": term or None,
            "with_interaction_dates": "true" if with_interaction_dates else None,
            "page_size": page_size, "page_token": page_token})

    # --- entities: reads (v2) ------------------------------------------------

    def get_person(self, person_id: Any, *, field_ids: Optional[List[str]] = None,
                   field_types: Optional[List[str]] = None) -> Any:
        """GET /v2/persons/{id} — no field data unless fields are requested."""
        return self._v2("GET", f"/persons/{_id(person_id, 'person_id')}",
                        params=self._fields(field_ids, field_types))

    def get_company(self, company_id: Any, *, field_ids: Optional[List[str]] = None,
                    field_types: Optional[List[str]] = None) -> Any:
        """GET /v2/companies/{id} — no field data unless fields are requested."""
        return self._v2("GET", f"/companies/{_id(company_id, 'company_id')}",
                        params=self._fields(field_ids, field_types))

    def get_opportunity(self, opportunity_id: Any) -> Any:
        """GET /v2/opportunities/{id} — name and list; field values live on its
        list entry (opportunities have no global fields)."""
        return self._v2("GET", f"/opportunities/{_id(opportunity_id, 'opportunity_id')}")

    def entity_list_entries(self, kind: str, entity_id: Any, *,
                            cursor: Optional[str] = None,
                            limit: Optional[int] = None) -> Any:
        """GET /v2/{persons|companies}/{id}/list-entries — every list the entity
        is on, with ALL its field values (list fields included)."""
        if kind == "opportunity":
            raise ValueError("An opportunity has a single list entry: read it from its list.")
        return self._v2("GET", f"/{self._plural(kind)}/{_id(entity_id, 'id')}/list-entries",
                        params={"cursor": cursor, "limit": _limit(limit)})

    def relationships(self, kind: str, entity_id: Any, *, cursor: Optional[str] = None,
                      limit: Optional[int] = None) -> Any:
        """GET /v2/{persons|companies}/{id}/relationships — connections with their
        `interactionScore` (0-1, ≥ 0.7 ≈ regular contact), strongest first."""
        if kind == "opportunity":
            raise ValueError("Relationships exist for persons and companies only.")
        return self._v2("GET", f"/{self._plural(kind)}/{_id(entity_id, 'id')}/relationships",
                        params={"cursor": cursor, "limit": _limit(limit)})

    def global_fields(self, kind: str, *, cursor: Optional[str] = None,
                      limit: Optional[int] = None) -> Any:
        """GET /v2/{persons|companies}/fields — global and enriched fields with
        their `valueType`."""
        if kind == "opportunity":
            raise ValueError("Opportunities have no global fields: use the list's fields.")
        return self._v2("GET", f"/{self._plural(kind)}/fields",
                        params={"cursor": cursor, "limit": _limit(limit)})

    def get_entity_v1(self, kind: str, entity_id: Any) -> Any:
        """GET /persons|organizations|opportunities/{id} (v1) — the record with
        its association ids (`organization_ids`, `person_ids`), which v2 does
        not return and which v1 updates REPLACE."""
        path = {"person": "persons", "company": "organizations",
                "opportunity": "opportunities"}[_choice("kind", kind, ENTITY_KINDS)]
        return self._v1("GET", f"/{path}/{_id(entity_id, 'id')}")

    # --- entities: writes ----------------------------------------------------

    def create_person(self, first_name: str, last_name: str,
                      emails: Optional[List[str]] = None,
                      organization_ids: Optional[List[Any]] = None) -> Any:
        """POST /persons (v1) — fails if one of the emails belongs to another person."""
        if not (first_name or "").strip() or not (last_name or "").strip():
            raise ValueError("`first_name` and `last_name` are both required.")
        return self._v1("POST", "/persons", json={
            "first_name": first_name, "last_name": last_name,
            "emails": list(emails or []),
            **({"organization_ids": _ids(organization_ids, "organization_ids")}
               if organization_ids else {})})

    def update_person(self, person_id: Any, *, first_name: Optional[str] = None,
                      last_name: Optional[str] = None,
                      emails: Optional[List[str]] = None,
                      organization_ids: Optional[List[Any]] = None) -> Any:
        """PUT /persons/{id} (v1). `emails` and `organization_ids` REPLACE the
        current sets."""
        body = {k: v for k, v in {
            "first_name": first_name, "last_name": last_name,
            "emails": list(emails) if emails is not None else None,
            "organization_ids": _ids(organization_ids, "organization_ids"),
        }.items() if v is not None}
        if not body:
            raise ValueError("Nothing to update.")
        return self._v1("PUT", f"/persons/{_id(person_id, 'person_id')}", json=body)

    def create_organization(self, name: str, domain: Optional[str] = None,
                            person_ids: Optional[List[Any]] = None) -> Any:
        """POST /organizations (v1). Affinity does NOT refuse a duplicate domain."""
        if not (name or "").strip():
            raise ValueError("`name` is required.")
        body: Dict[str, Any] = {"name": name}
        if domain:
            body["domain"] = domain
        if person_ids:
            body["person_ids"] = _ids(person_ids, "person_ids")
        return self._v1("POST", "/organizations", json=body)

    def update_organization(self, organization_id: Any, *, name: Optional[str] = None,
                            domain: Optional[str] = None,
                            person_ids: Optional[List[Any]] = None) -> Any:
        """PUT /organizations/{id} (v1). `person_ids` REPLACES the current set.
        Global organizations cannot be changed."""
        body = {k: v for k, v in {
            "name": name, "domain": domain,
            "person_ids": _ids(person_ids, "person_ids")}.items() if v is not None}
        if not body:
            raise ValueError("Nothing to update.")
        return self._v1("PUT", f"/organizations/{_id(organization_id, 'organization_id')}",
                        json=body)

    def create_opportunity(self, name: str, list_id: Any,
                           person_ids: Optional[List[Any]] = None,
                           organization_ids: Optional[List[Any]] = None) -> Any:
        """POST /opportunities (v1) — the only way an opportunity joins its
        (opportunity-type) list."""
        if not (name or "").strip():
            raise ValueError("`name` is required.")
        body: Dict[str, Any] = {"name": name, "list_id": _id(list_id, "list_id")}
        if person_ids:
            body["person_ids"] = _ids(person_ids, "person_ids")
        if organization_ids:
            body["organization_ids"] = _ids(organization_ids, "organization_ids")
        return self._v1("POST", "/opportunities", json=body)

    def update_opportunity(self, opportunity_id: Any, *, name: Optional[str] = None,
                           person_ids: Optional[List[Any]] = None,
                           organization_ids: Optional[List[Any]] = None) -> Any:
        """PUT /opportunities/{id} (v1). Association arrays REPLACE the current sets."""
        body = {k: v for k, v in {
            "name": name, "person_ids": _ids(person_ids, "person_ids"),
            "organization_ids": _ids(organization_ids, "organization_ids"),
        }.items() if v is not None}
        if not body:
            raise ValueError("Nothing to update.")
        return self._v1("PUT", f"/opportunities/{_id(opportunity_id, 'opportunity_id')}",
                        json=body)

    def update_entity_fields(self, kind: str, entity_id: Any,
                             updates: List[Dict[str, Any]]) -> Any:
        """PATCH /v2/{persons|companies}/{id}/fields — up to 100 global field
        values at once. Each update is `{"id": field_id, "value": field_value(...)}`."""
        if kind == "opportunity":
            raise ValueError("Opportunities have no global fields: write them on the list entry.")
        return self._v2("PATCH", f"/{self._plural(kind)}/{_id(entity_id, 'id')}/fields",
                        json={"operation": "update-fields",
                              "updates": self._updates(updates)})
