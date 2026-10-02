"""Affinity notes (v2) and interactions — emails, meetings, calls, chat messages
with one external entity (v1).

Composed into `AffinityClient`, which provides the transport (`_v1`, `_v2`,
`_fields`, `_plural`). Contract and protocol facts: `../client.py`.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..values import (INTERACTION_DIRECTIONS, INTERACTION_TYPES, _choice, _id, _iso, _limit, _note_id,
                      _note_refs, note_html)


class _ActivityMixin:
    # --- notes (v2) ----------------------------------------------------------

    def list_notes(self, *, kind: Optional[str] = None, entity_id: Optional[Any] = None,
                   filter: Optional[str] = None, cursor: Optional[str] = None,
                   limit: Optional[int] = None) -> Any:
        """GET /v2/notes, or /v2/{persons|companies|opportunities}/{id}/notes for
        one entity (direct notes, notes on its meetings, mentions). `filter`
        supports `creator.id=`, `createdAt>…`, `updatedAt<…`."""
        params = {"filter": filter or None, "cursor": cursor, "limit": _limit(limit)}
        if kind is None and entity_id is None:
            return self._v2("GET", "/notes", params=params)
        if kind is None or entity_id is None:
            raise ValueError("`kind` and `entity_id` go together.")
        return self._v2("GET", f"/{self._plural(kind)}/{_id(entity_id, 'entity_id')}/notes",
                        params=params)

    def get_note(self, note_id: Any) -> Any:
        """GET /v2/notes/{id}."""
        return self._v2("GET", f"/notes/{_note_id(note_id)}")

    def create_note(self, content: str, *, person_ids: Optional[List[Any]] = None,
                    company_ids: Optional[List[Any]] = None,
                    opportunity_ids: Optional[List[Any]] = None) -> Any:
        """POST /v2/notes — a note attached to at least one entity. `content` is
        plain text or allowlisted HTML (see `note_html`)."""
        refs = _note_refs(person_ids, company_ids, opportunity_ids)
        if not any(refs.values()):
            raise ValueError("Attach the note to at least one person, company or opportunity.")
        return self._v2("POST", "/notes", json={
            "type": "entities", "content": {"html": note_html(content)},
            **{k: v for k, v in refs.items() if v}})

    def update_note(self, note_id: Any, *, content: Optional[str] = None,
                    person_ids: Optional[List[Any]] = None,
                    company_ids: Optional[List[Any]] = None,
                    opportunity_ids: Optional[List[Any]] = None) -> Any:
        """POST /v2/notes/{id} — partial update (204). An association array
        REPLACES that kind (`[]` clears it). Notes with @mentions cannot have
        their content changed."""
        body: Dict[str, Any] = {k: v for k, v in _note_refs(
            person_ids, company_ids, opportunity_ids).items() if v is not None}
        if content is not None:
            body["content"] = {"html": note_html(content)}
        if not body:
            raise ValueError("Nothing to update.")
        return self._v2("POST", f"/notes/{_note_id(note_id)}", json=body)

    def delete_note(self, note_id: Any) -> Any:
        """DELETE /v2/notes/{id}."""
        return self._v2("DELETE", f"/notes/{_note_id(note_id)}")

    # --- interactions (v1, per entity) --------------------------------------

    def list_interactions(self, interaction_type: str, *, start_time: Any, end_time: Any,
                          person_id: Optional[Any] = None,
                          organization_id: Optional[Any] = None,
                          opportunity_id: Optional[Any] = None,
                          direction: Optional[str] = None,
                          page_size: Optional[int] = None,
                          page_token: Optional[str] = None) -> Any:
        """GET /interactions (v1) — emails, meetings, calls or chat messages with
        ONE external person, organization or opportunity, within a window of at
        most one year. The answer is keyed by type (`emails`, `events`, …)."""
        type_code = INTERACTION_TYPES[_choice("interaction_type", interaction_type,
                                              INTERACTION_TYPES)]
        anchors = {"person_id": person_id, "organization_id": organization_id,
                   "opportunity_id": opportunity_id}
        given = {k: v for k, v in anchors.items() if v is not None}
        if len(given) != 1:
            raise ValueError("Give exactly one of person_id, organization_id, opportunity_id.")
        start, end = _iso(start_time, "start_time"), _iso(end_time, "end_time")
        if not start or not end:
            raise ValueError("`start_time` and `end_time` are required (at most one year apart).")
        params: Dict[str, Any] = {"type": type_code, "start_time": start, "end_time": end,
                                  "page_size": page_size, "page_token": page_token}
        params.update({k: _id(v, k) for k, v in given.items()})
        if direction is not None:
            params["direction"] = INTERACTION_DIRECTIONS[
                _choice("direction", direction, INTERACTION_DIRECTIONS)]
        return self._v1("GET", "/interactions", params=params)
