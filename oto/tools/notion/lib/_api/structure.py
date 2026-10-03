"""Databases, data sources and views.

Never instantiated alone: composed into `NotionClient`, which provides
`_request` and `resolve_data_source`.

Since API 2025-09-03 a database is a container: its columns live on its data
source(s). Schema writes go to `PATCH data_sources/{id}`; the database itself
only carries title, description, icon and trash status. Views hang off the
database and point at one of its data sources.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

_VIEW_TYPES = ("table", "board", "list", "calendar", "timeline", "gallery",
               "form", "chart", "map", "dashboard")


def _rich_text(text: str) -> list:
    return [{"type": "text", "text": {"content": text}}]


class _StructureMixin:
    """Create / update databases, edit their columns, manage views."""

    def _database_and_source(self, database_id: str) -> Tuple[Optional[str], Dict]:
        """(database id, data source object) for a database or data source id.

        The database id is None when the data source does not hang directly
        off a database (e.g. an externally synced source).
        """
        data_source = self.resolve_data_source(database_id)
        parent = data_source.get('parent') or {}
        db_id = parent.get('database_id') if parent.get('type') == 'database_id' else None
        return (db_id.replace('-', '') if db_id else None), data_source

    def create_database(self, parent_page_id: str, title: Optional[str] = None,
                        properties: Optional[Dict] = None, is_inline: bool = False,
                        database_type: Optional[str] = None) -> Dict:
        """Create a database under a page.

        Args:
            properties: column schema, e.g. {"Name": {"title": {}},
                "Status": {"select": {"options": [{"name": "Todo"}]}}}.
                A title column is added when none is given.
            database_type: "tasks", "projects" or "skills" — Notion's own
                schema; cannot be combined with `properties`.
        """
        if database_type and properties:
            raise ValueError("database_type and properties cannot be combined.")
        data: Dict[str, Any] = {
            "parent": {"type": "page_id", "page_id": parent_page_id.replace('-', '')},
            "is_inline": is_inline,
        }
        if title:
            data["title"] = _rich_text(title)
        if database_type:
            data["database_type"] = database_type
        else:
            schema = dict(properties or {})
            if not any(isinstance(v, dict) and 'title' in v for v in schema.values()):
                schema = {"Name": {"title": {}}, **schema}
            data["initial_data_source"] = {"properties": schema}
        return self._request('POST', 'databases', data=data, use_cache=False)

    def update_database(self, database_id: str, properties: Optional[Dict] = None,
                        title: Optional[str] = None,
                        description: Optional[str] = None,
                        in_trash: Optional[bool] = None) -> Dict:
        """Edit a database: its columns and/or its title, description, trash.

        Args:
            properties: column changes, applied to the data source.
                Add: {"Due": {"date": {}}}. Rename: {"Due": {"name": "Deadline"}}.
                Remove: {"Due": None}. Change type: {"Est": {"number": {}}}.
                Select options: the FULL list to keep ({"id": …} or {"name": …}).
        """
        db_id, data_source = self._database_and_source(database_id)
        result: Dict[str, Any] = {}
        # Two requests when both columns and title/description change: the
        # columns go first; if the second fails, they are already applied.
        if properties:
            result["data_source"] = self._request(
                'PATCH', f"data_sources/{data_source['id'].replace('-', '')}",
                data={"properties": properties}, use_cache=False)
        meta: Dict[str, Any] = {}
        if title is not None:
            meta["title"] = _rich_text(title)
        if description is not None:
            meta["description"] = _rich_text(description)
        if in_trash is not None:
            meta["in_trash"] = in_trash
        if meta and not db_id:
            raise ValueError(
                f"{database_id}: this data source does not belong directly to a "
                f"database — its title cannot be changed here.")
        if meta:
            result["database"] = self._request(
                'PATCH', f'databases/{db_id}', data=meta, use_cache=False)
        if not result:
            raise ValueError("Nothing to update: pass properties, title, "
                             "description or in_trash.")
        return result

    # --- views ------------------------------------------------------------

    def list_views(self, database_id: str, start_cursor: Optional[str] = None) -> Dict:
        """Views of a database (database or data source id)."""
        db_id, data_source = self._database_and_source(database_id)
        params: Dict[str, Any] = (
            {"database_id": db_id} if db_id else {"data_source_id": data_source['id']})
        params["page_size"] = 100
        if start_cursor:
            params["start_cursor"] = start_cursor
        return self._request('GET', 'views', params=params, use_cache=False)

    def get_view(self, view_id: str) -> Dict:
        return self._request('GET', f"views/{view_id.replace('-', '')}", use_cache=False)

    def create_view(self, database_id: str, name: str, view_type: str = "table",
                    filter_obj: Optional[Dict] = None, sorts: Optional[list] = None,
                    configuration: Optional[Dict] = None) -> Dict:
        """Add a view to a database.

        Args:
            view_type: table, board, list, calendar, timeline, gallery, form,
                chart, map or dashboard.
            filter_obj / sorts: same format as a data source query.
            configuration: type-specific settings (visible properties,
                group by…), as Notion's view object describes them.
        """
        if view_type not in _VIEW_TYPES:
            raise ValueError(f"view type {view_type!r}: expected one of {', '.join(_VIEW_TYPES)}.")
        db_id, data_source = self._database_and_source(database_id)
        if not db_id:
            raise ValueError(
                f"{database_id}: this data source does not belong directly to a "
                f"database — pass the database id.")
        data: Dict[str, Any] = {
            "database_id": db_id,
            "data_source_id": data_source['id'],
            "name": name,
            "type": view_type,
        }
        if filter_obj is not None:
            data["filter"] = filter_obj
        if sorts is not None:
            data["sorts"] = sorts
        if configuration is not None:
            data["configuration"] = configuration
        return self._request('POST', 'views', data=data, use_cache=False)

    def update_view(self, view_id: str, name: Optional[str] = None,
                    filter_obj: Optional[Dict] = None, sorts: Optional[list] = None,
                    configuration: Optional[Dict] = None,
                    clear: Optional[list] = None) -> Dict:
        """Change a view's name, filter, sorts or configuration.

        Args:
            clear: fields to reset, among "filter" and "sorts".
        """
        data: Dict[str, Any] = {}
        if name is not None:
            data["name"] = name
        if filter_obj is not None:
            data["filter"] = filter_obj
        if sorts is not None:
            data["sorts"] = sorts
        if configuration is not None:
            data["configuration"] = configuration
        for field in clear or []:
            if field not in ("filter", "sorts"):
                raise ValueError(f"clear {field!r}: only 'filter' and 'sorts' can be cleared.")
            if field in data:
                raise ValueError(f"{field!r} is both set and cleared.")
            data[field] = None
        if not data:
            raise ValueError("Nothing to update on the view.")
        return self._request('PATCH', f"views/{view_id.replace('-', '')}",
                             data=data, use_cache=False)

    def delete_view(self, view_id: str) -> Dict:
        """Delete a view (the last view of a database cannot be deleted)."""
        return self._request('DELETE', f"views/{view_id.replace('-', '')}", use_cache=False)
