"""Affinity lists: fields, saved views, list entries and their field values,
dropdown options.

Composed into `AffinityClient`, which provides the transport (`_v1`, `_v2`,
`_fields`, `_plural`). Contract and protocol facts: `../client.py`.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import quote

from ..values import (MAX_FIELD_UPDATES, _field_id, _id, _limit)


class _ListsMixin:
    # --- lists ---------------------------------------------------------------

    def list_lists(self, *, term: Optional[str] = None, cursor: Optional[str] = None,
                   limit: Optional[int] = None) -> Any:
        """GET /v2/lists — lists the key's user can see."""
        return self._v2("GET", "/lists", params={
            "term": term or None, "cursor": cursor, "limit": _limit(limit)})

    def get_list(self, list_id: Any) -> Any:
        """GET /v2/lists/{id} — name, type (person/company/opportunity), owner."""
        return self._v2("GET", f"/lists/{_id(list_id, 'list_id')}")

    def list_fields(self, list_id: Any, *, cursor: Optional[str] = None,
                    limit: Optional[int] = None) -> Any:
        """GET /v2/lists/{id}/fields — every field shown on the list (list,
        global, enriched…) with its `valueType` and `isRequired`."""
        return self._v2("GET", f"/lists/{_id(list_id, 'list_id')}/fields",
                        params={"cursor": cursor, "limit": _limit(limit)})

    def list_saved_views(self, list_id: Any, *, cursor: Optional[str] = None,
                         limit: Optional[int] = None) -> Any:
        """GET /v2/lists/{id}/saved-views."""
        return self._v2("GET", f"/lists/{_id(list_id, 'list_id')}/saved-views",
                        params={"cursor": cursor, "limit": _limit(limit)})

    def list_entries(self, list_id: Any, *, field_ids: Optional[List[str]] = None,
                     field_types: Optional[List[str]] = None,
                     cursor: Optional[str] = None, limit: Optional[int] = None) -> Any:
        """GET /v2/lists/{id}/list-entries — rows of the list. Needs the "Export
        data from Lists" permission. No field data unless fields are requested."""
        params = self._fields(field_ids, field_types)
        params.update(cursor=cursor, limit=_limit(limit))
        return self._v2("GET", f"/lists/{_id(list_id, 'list_id')}/list-entries", params=params)

    def saved_view_entries(self, list_id: Any, view_id: Any, *,
                           cursor: Optional[str] = None,
                           limit: Optional[int] = None) -> Any:
        """GET /v2/lists/{id}/saved-views/{view}/list-entries — the view's rows,
        filters and columns as saved in the app."""
        return self._v2(
            "GET",
            f"/lists/{_id(list_id, 'list_id')}/saved-views/{_id(view_id, 'view_id')}/list-entries",
            params={"cursor": cursor, "limit": _limit(limit)})

    def get_list_entry(self, list_id: Any, entry_id: Any, *,
                       field_ids: Optional[List[str]] = None,
                       field_types: Optional[List[str]] = None) -> Any:
        """GET /v2/lists/{id}/list-entries/{entry} — one row with its entity."""
        return self._v2(
            "GET", f"/lists/{_id(list_id, 'list_id')}/list-entries/{_id(entry_id, 'entry_id')}",
            params=self._fields(field_ids, field_types))

    def add_list_entry(self, list_id: Any, entity_id: Any,
                       creator_id: Optional[Any] = None) -> Any:
        """POST /lists/{id}/list-entries (v1) — add a person or organization.
        Opportunities cannot be added: create them with `create_opportunity`.
        The same entity can be added twice: check first."""
        body: Dict[str, Any] = {"entity_id": _id(entity_id, "entity_id")}
        if creator_id is not None:
            body["creator_id"] = _id(creator_id, "creator_id")
        return self._v1("POST", f"/lists/{_id(list_id, 'list_id')}/list-entries", json=body)

    def remove_list_entry(self, list_id: Any, entry_id: Any) -> Any:
        """DELETE /lists/{id}/list-entries/{entry} (v1) — also deletes the entry's
        list-field values; on an OPPORTUNITY list, deletes the opportunity."""
        return self._v1(
            "DELETE", f"/lists/{_id(list_id, 'list_id')}/list-entries/{_id(entry_id, 'entry_id')}")

    def update_list_entry_fields(self, list_id: Any, entry_id: Any,
                                 updates: List[Dict[str, Any]]) -> Any:
        """PATCH /v2/lists/{id}/list-entries/{entry}/fields — up to 100 field
        values of ONE entry. Each update is `{"id": field_id, "value": ...}`."""
        return self._v2(
            "PATCH",
            f"/lists/{_id(list_id, 'list_id')}/list-entries/{_id(entry_id, 'entry_id')}/fields",
            json={"operation": "update-fields", "updates": self._updates(updates)})

    @staticmethod
    def _updates(updates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not updates:
            raise ValueError("No field update given.")
        if len(updates) > MAX_FIELD_UPDATES:
            raise ValueError(
                f"At most {MAX_FIELD_UPDATES} field updates per request; got {len(updates)}.")
        out = []
        for u in updates:
            value = u.get("value")
            if not isinstance(value, dict) or "type" not in value or "data" not in value:
                raise ValueError(
                    "Each update needs `value` = {type, data}: build it with field_value().")
            out.append({"id": _field_id(u.get("id")), "value": value})
        return out

    def dropdown_options(self, field_id: str, *, list_id: Optional[Any] = None,
                         kind: Optional[str] = None, cursor: Optional[str] = None,
                         limit: Optional[int] = None) -> Any:
        """Options of a dropdown field: GET /v2/lists/{id}/fields/{f}/dropdown-options
        for a list field, /v2/{persons|companies}/fields/{f}/dropdown-options for
        a global one."""
        fid = quote(_field_id(field_id), safe="")
        params = {"cursor": cursor, "limit": _limit(limit)}
        if list_id is not None:
            return self._v2("GET", f"/lists/{_id(list_id, 'list_id')}/fields/{fid}/dropdown-options",
                            params=params)
        if kind in ("person", "company"):
            return self._v2("GET", f"/{self._plural(kind)}/fields/{fid}/dropdown-options",
                            params=params)
        raise ValueError("Give `list_id` (list field) or `kind` person|company (global field).")
