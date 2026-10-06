"""Brevo — native CRM: deals, companies, tasks, notes, pipelines.

**Generic** surface (`entity` as a parameter) rather than 4×4 methods: the
four objects share list/get/create/update. Three API asymmetries are
absorbed here and do not leak to the caller:

- the path: `companies` lives at `/companies`, the other three under `/crm/…`;
- pagination: `companies` paginates by `page` (1-based), the others by `offset`;
- the filter prefix: `filters[…]` for deals/companies, `filter[…]` for tasks,
  flat parameters for notes.

Creation bodies differ too much to be uniformized: `payload` is passed
as-is to the API (Brevo camelCase keys), with the required fields documented below.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ._base import _BrevoBase

# entity → (collection path, filter prefix, paginates by page?)
_ENTITIES: Dict[str, tuple] = {
    "deals": ("/crm/deals", "filters", False),
    "companies": ("/companies", "filters", True),
    "tasks": ("/crm/tasks", "filter", False),
    "notes": ("/crm/notes", None, False),
}

# entity → fields required at creation (for a useful error message on the agent side)
REQUIRED_FIELDS: Dict[str, tuple] = {
    "deals": ("name",),
    "companies": ("name",),
    "tasks": ("name", "taskTypeId", "date"),
    "notes": ("text",),
}


class CrmMixin(_BrevoBase):

    @staticmethod
    def _entity(entity: str) -> tuple:
        try:
            return _ENTITIES[entity]
        except KeyError:
            raise ValueError(
                f"unknown entity {entity!r} — expected: {', '.join(_ENTITIES)}")

    def crm_list(
        self,
        entity: str,
        limit: int = 50,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        sort: Optional[str] = None,
        sort_by: Optional[str] = None,
        modified_since: Optional[str] = None,
        created_since: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List CRM objects.

        Args:
            entity: `deals` | `companies` | `tasks` | `notes`.
            filters: RAW filter keys of the entity, without the prefix. E.g.
                deals → `{"attributes.deal_name": "Acme", "linkedContactsIds": "12"}`;
                companies → `{"attributes.name": "Acme"}`;
                tasks → `{"type": "call", "status": "done", "contacts": "12"}`;
                notes → `{"entity": "deals", "entityIds": "abc123"}` (flat params).
            offset: converted to `page` for `companies` (the API paginates by page there).
            sort_by: sort field (`deals`/`companies`/`tasks`).
        """
        path, prefix, by_page = self._entity(entity)
        params: Dict[str, Any] = {"limit": limit, "sort": sort, "sortBy": sort_by,
                                  "modifiedSince": modified_since,
                                  "createdSince": created_since}
        if by_page:
            params["page"] = (offset // limit) + 1 if limit else 1
        else:
            params["offset"] = offset
        for key, value in (filters or {}).items():
            params[f"{prefix}[{key}]" if prefix else key] = value
        return self._request("GET", path, params=self._clean(params))

    def crm_get(self, entity: str, object_id: str) -> Dict[str, Any]:
        """Fetch a CRM object by id."""
        path, _, _ = self._entity(entity)
        return self._request("GET", f"{path}/{object_id}")

    def crm_create(self, entity: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Create a CRM object. Returns `{"id": …}`.

        `payload` in Brevo camelCase. Required fields:
        - **deals**: `name` (+ `attributes`, `linkedContactsIds`, `linkedCompaniesIds`)
        - **companies**: `name` (+ `attributes`, `countryCode`, `linkedContactsIds`)
        - **tasks**: `name`, `taskTypeId` (see `task_types`), `date` (ISO 8601)
          (+ `contactsIds`, `dealsIds`, `companiesIds`, `assignToId`, `notes`, `done`)
        - **notes**: `text` (+ `contactIds`, `dealIds`, `companyIds`)
        """
        path, _, _ = self._entity(entity)
        missing = [f for f in REQUIRED_FIELDS[entity] if not payload.get(f)]
        if missing:
            raise ValueError(
                f"missing required fields for {entity}: {', '.join(missing)}")
        return self._request("POST", path, json=payload)

    def crm_update(self, entity: str, object_id: str,
                   payload: Dict[str, Any]) -> Dict[str, Any]:
        """Update (PATCH) a CRM object — provided fields only.

        To attach/detach linked objects, use `crm_link` (dedicated endpoint
        on deals and companies).
        """
        path, _, _ = self._entity(entity)
        return self._request("PATCH", f"{path}/{object_id}", json=payload)

    def crm_link(
        self,
        entity: str,
        object_id: str,
        link_contact_ids: Optional[List[int]] = None,
        unlink_contact_ids: Optional[List[int]] = None,
        link_ids: Optional[List[str]] = None,
        unlink_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Attach/detach linked objects — `deals` and `companies` only.

        `link_ids`/`unlink_ids` target the complementary object: the **companies**
        of a deal, the **deals** of a company.
        """
        if entity not in ("deals", "companies"):
            raise ValueError("crm_link only applies to deals and companies.")
        path, _, _ = self._entity(entity)
        if entity == "deals":
            body = self._clean({
                "linkContactIds": link_contact_ids,
                "unlinkContactIds": unlink_contact_ids,
                "linkCompanyIds": link_ids, "unlinkCompanyIds": unlink_ids})
        else:
            body = self._clean({
                "linkContactIds": link_contact_ids,
                "unlinkContactIds": unlink_contact_ids,
                "linkDealsIds": link_ids, "unlinkDealsIds": unlink_ids})
        return self._request("PATCH", f"{path}/link-unlink/{object_id}", json=body)

    # --- Metadata -------------------------------------------------------------

    def pipelines(self) -> Dict[str, Any]:
        """All deal pipelines and their stages (stage `id` for `deal_stage`)."""
        return self._request("GET", "/crm/pipeline/details/all")

    def task_types(self) -> Dict[str, Any]:
        """The account's task types (their `id` is required to create a task)."""
        return self._request("GET", "/crm/tasktypes")

    def crm_attributes(self, entity: str) -> Dict[str, Any]:
        """Custom attributes declared on `deals` or `companies`."""
        if entity not in ("deals", "companies"):
            raise ValueError("crm_attributes only applies to deals and companies.")
        return self._request("GET", f"/crm/attributes/{entity}")
