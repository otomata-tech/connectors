"""Page content: Markdown read/edit, single block update and delete.

Never instantiated alone: composed into `NotionClient`, which provides
`_request`.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .._ids import notion_id


class _ContentMixin:
    """Markdown view of a page, block edits."""

    def get_markdown(self, page_id: str) -> Dict:
        """Page content as Notion-flavoured Markdown.

        `truncated: true` + `unknown_block_ids`: parts not loaded (very large
        page, or child pages not shared) — each id can be read the same way.
        """
        return self._request('GET', f"pages/{notion_id(page_id, 'page_id')}/markdown",
                             use_cache=False)

    def edit_markdown(self, page_id: str,
                      replacements: Optional[List[Dict]] = None,
                      new_content: Optional[str] = None,
                      insert: Optional[str] = None,
                      position: str = "end",
                      allow_deleting_content: bool = False) -> Dict:
        """Edit a page's content as Markdown — exactly one mode.

        Args:
            replacements: search-and-replace, [{"old_str", "new_str",
                "replace_all_matches"?}] (at most 100). Each `old_str` must
                match once unless `replace_all_matches`.
            new_content: replace the WHOLE page content.
            insert: Markdown to add, at `position` "start" or "end".
            allow_deleting_content: needed when the edit would remove child
                pages or databases (refused otherwise).
        """
        if replacements is not None and not replacements:
            raise ValueError("replacements is empty.")
        modes = [m for m in (replacements, new_content, insert) if m is not None]
        if len(modes) != 1:
            raise ValueError("Give exactly one of replacements, new_content or insert.")
        body: Dict[str, Any]
        if replacements is not None:
            body = {"type": "update_content", "update_content": {
                "content_updates": replacements,
                "allow_deleting_content": allow_deleting_content}}
        elif new_content is not None:
            body = {"type": "replace_content", "replace_content": {
                "new_str": new_content,
                "allow_deleting_content": allow_deleting_content}}
        else:
            if position not in ("start", "end"):
                raise ValueError(f"position {position!r}: expected 'start' or 'end'.")
            body = {"type": "insert_content", "insert_content": {
                "content": insert, "position": {"type": position}}}
        return self._request('PATCH', f"pages/{notion_id(page_id, 'page_id')}/markdown",
                             data=body, use_cache=False)

    def update_block(self, block_id: str, block: Dict) -> Dict:
        """Replace fields of one block, e.g. {"paragraph": {"rich_text": […]}}
        or {"to_do": {"checked": true}}. Children are not editable here."""
        return self._request('PATCH', f"blocks/{notion_id(block_id, 'block_id')}",
                             data=block, use_cache=False)

    def delete_block(self, block_id: str) -> Dict:
        """Move a block to the trash (restorable from Notion)."""
        return self._request('DELETE', f"blocks/{notion_id(block_id, 'block_id')}",
                             use_cache=False)
