"""Comments, users and moving pages.

Never instantiated alone: composed into `NotionClient`, which provides
`_request` and `resolve_data_source`.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .._ids import notion_id

_COMMENT_CAPABILITY = (
    "\n  Reading and inserting comments are capabilities that are OFF by default "
    "on a Notion integration: they are turned on in the integration's settings.")


class _CollabMixin:
    """Comments, workspace users, page moves."""

    def _comments_request(self, method: str, endpoint: str, **kwargs) -> Dict:
        try:
            return self._request(method, endpoint, use_cache=False, **kwargs)
        except Exception as e:
            if "(403)" in str(e):
                raise Exception(f"{e}{_COMMENT_CAPABILITY}") from e
            raise

    def list_comments(self, block_id: str, start_cursor: Optional[str] = None) -> Dict:
        """Open (unresolved) comments on a page or block."""
        params: Dict[str, Any] = {"block_id": notion_id(block_id, 'block_id'), "page_size": 100}
        if start_cursor:
            params["start_cursor"] = start_cursor
        return self._comments_request('GET', 'comments', params=params)

    def add_comment(self, text: str, page_id: Optional[str] = None,
                    block_id: Optional[str] = None,
                    discussion_id: Optional[str] = None) -> Dict:
        """Comment on a page, on a block, or reply in a discussion.

        Exactly one target. `text` is Markdown (inline formatting only).
        """
        targets = [t for t in (page_id, block_id, discussion_id) if t]
        if len(targets) != 1:
            raise ValueError("Give exactly one of page_id, block_id or discussion_id.")
        data: Dict[str, Any] = {"markdown": text}
        if page_id:
            data["parent"] = {"page_id": notion_id(page_id, 'page_id')}
        elif block_id:
            data["parent"] = {"block_id": notion_id(block_id, 'block_id')}
        else:
            data["discussion_id"] = discussion_id
        return self._comments_request('POST', 'comments', data=data)

    def update_comment(self, comment_id: str, text: str) -> Dict:
        """Rewrite a comment (only one the integration created)."""
        return self._comments_request(
            'PATCH', f"comments/{notion_id(comment_id, 'comment_id')}", data={"markdown": text})

    def delete_comment(self, comment_id: str) -> Dict:
        return self._comments_request('DELETE', f"comments/{notion_id(comment_id, 'comment_id')}")

    def list_users(self, start_cursor: Optional[str] = None) -> Dict:
        """Workspace members, guests and bots."""
        params: Dict[str, Any] = {"page_size": 100}
        if start_cursor:
            params["start_cursor"] = start_cursor
        return self._request('GET', 'users', params=params, use_cache=False)

    def move_page(self, page_id: str, parent_id: str, parent_type: str = "page") -> Dict:
        """Move a page under another page, or into a database.

        Args:
            parent_type: "page", or "database" (a database or data source id).
        """
        if parent_type == "page":
            parent = {"type": "page_id", "page_id": notion_id(parent_id, 'parent_id')}
        elif parent_type in ("database", "data_source"):
            data_source = self.resolve_data_source(parent_id)
            parent = {"type": "data_source_id", "data_source_id": data_source['id']}
        else:
            raise ValueError(f"parent_type {parent_type!r}: expected 'page' or 'database'.")
        return self._request('POST', f"pages/{notion_id(page_id, 'page_id')}/move",
                             data={"parent": parent}, use_cache=False)
