"""Page writes: create, update, add blocks.

Never instantiated alone: composed into `NotionClient`, which provides
`_request` and `resolve_data_source`.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# Notion caps `children` at 100 blocks per request (create page / append).
_MAX_CHILDREN = 100


class _PagesMixin:
    """Create pages and database rows, edit them, add blocks."""

    def create_page(self, parent_id: str, parent_type: str,
                    title: str, properties: Optional[Dict] = None,
                    content: Optional[list] = None) -> Dict:
        """Create new page.

        Args:
            parent_type: "page", or "database"/"data_source" for a database
                row — either a database id or a data source id works.
            content: block objects; more than 100 are appended in batches.
        """
        parent_id = parent_id.replace('-', '')
        properties = dict(properties or {})

        if parent_type in ("database", "data_source"):
            data_source = self.resolve_data_source(parent_id)
            parent = {"type": "data_source_id", "data_source_id": data_source['id']}
            title_prop = next(
                (name for name, prop in (data_source.get('properties') or {}).items()
                 if prop.get('type') == 'title'),
                "Name",
            )
        elif parent_type == "page":
            parent = {"page_id": parent_id}
            title_prop = "title"
        else:
            raise ValueError(
                f"parent_type {parent_type!r}: expected 'page' or 'database'.")

        # The caller may already set the title column in `properties`.
        if not any(isinstance(v, dict) and 'title' in v for v in properties.values()):
            properties[title_prop] = {"title": [{"text": {"content": title}}]}

        data = {"parent": parent, "properties": properties}
        content = list(content or [])
        if content:
            data["children"] = content[:_MAX_CHILDREN]

        page = self._request('POST', 'pages', data=data, use_cache=False)
        if len(content) > _MAX_CHILDREN:
            self.append_blocks(page['id'], content[_MAX_CHILDREN:])
        return page

    def update_page(self, page_id: str, properties: Optional[Dict] = None,
                    archived: Optional[bool] = None, in_trash: Optional[bool] = None,
                    icon: Optional[Dict] = None, cover: Optional[Dict] = None) -> Dict:
        """Update page properties, icon, cover, or move it to/from the trash.

        `archived` is the old name of `in_trash` (removed in API 2026-03-11).
        """
        page_id = page_id.replace('-', '')
        data: Dict[str, Any] = {}

        if properties:
            data["properties"] = properties
        if in_trash is None:
            in_trash = archived
        if in_trash is not None:
            data["in_trash"] = in_trash
        if icon is not None:
            data["icon"] = icon
        if cover is not None:
            data["cover"] = cover

        return self._request('PATCH', f'pages/{page_id}', data=data, use_cache=False)

    def append_blocks(self, page_id: str, blocks: list,
                      position: Optional[str] = None) -> Dict:
        """Add blocks to a page/block: at the end, at the start, or after a block.

        Args:
            position: None or "end" (default), "start", or the id of an
                existing child block to insert right after it.
        """
        page_id = page_id.replace('-', '')
        result: Dict = {}
        appended: list = []
        for i in range(0, len(blocks), _MAX_CHILDREN):
            data: Dict[str, Any] = {"children": blocks[i:i + _MAX_CHILDREN]}
            if position in ("start", "end"):
                data["position"] = {"type": position}
            elif position:
                data["position"] = {"type": "after_block",
                                    "after_block": {"id": position}}
            result = self._request(
                'PATCH', f'blocks/{page_id}/children', data=data, use_cache=False)
            appended.extend(result.get('results', []))
            if position not in (None, "end") and appended:
                position = appended[-1]['id']  # keep the batches in order
        if len(blocks) > _MAX_CHILDREN:
            result = {**result, "results": appended}
        return result
