"""Productlane tags and reply templates — the taxonomy of threads.

This mixin is never instantiated on its own: it is composed into `ProductlaneClient`,
which provides the transport (`_request`, `_list`).

⚠️ **Two families of tags have almost the same name, and are NOT the
same thing**: the ones here (`/tags`) go on THREADS and live in
groups (`tag_group_id` mandatory on creation); the ones in `changelogs.py`
(`/changelog-tags`) go on CHANGELOGS, have no group, and
belong to the Scale plan. Mixing them up yields a 400 whose message does not say
which of the two you meant.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


class _TaxonomyMixin:
    """Thread tags, tag groups, reply snippets."""

    # --- thread tags ---------------------------------------------------------

    def list_tags(self, limit: Optional[int] = None,
                  cursor: Optional[str] = None,
                  name_contains: Optional[str] = None,
                  tag_group_id: Optional[str] = None,
                  created_after: Optional[str] = None,
                  created_before: Optional[str] = None,
                  updated_after: Optional[str] = None,
                  updated_before: Optional[str] = None) -> Any:
        """GET /tags — THREAD tags. Scope `tags:read`."""
        return self._list("/tags", limit, cursor, {
            "name_contains": name_contains, "tag_group_id": tag_group_id,
            "created_after": created_after, "created_before": created_before,
            "updated_after": updated_after, "updated_before": updated_before,
        })

    def get_tag(self, tag_id: str) -> Any:
        """GET /tags/{id} — one tag. Scope `tags:read`."""
        return self._request("GET", f"/tags/{tag_id}")

    def create_tag(self, name: str, color: str, icon: str,
                   tag_group_id: str) -> Any:
        """POST /tags — create a thread tag. Scope `tags:write`.

        All **four** fields are required by upstream, `tag_group_id` included:
        a thread tag always lives in a group (see `list_tag_groups`).
        They are named one by one rather than passed as a dict, so that an omission
        shows up at write time and not at the 400.
        """
        for label, value in (("name", name), ("color", color),
                             ("icon", icon), ("tag_group_id", tag_group_id)):
            if not value:
                raise ValueError(
                    f"`{label}` is required: the Productlane API requires name, color, "
                    "icon AND tag_group_id to create a thread tag.")
        return self._request("POST", "/tags", json={
            "name": name, "color": color, "icon": icon,
            "tag_group_id": tag_group_id})

    def update_tag(self, tag_id: str, payload: Dict[str, Any]) -> Any:
        """PATCH /tags/{id} — update a tag. Scope `tags:write`.

        Fields: `name`, `color`, `icon`, `tag_group_id` (move it to another group).
        """
        return self._request("PATCH", f"/tags/{tag_id}", json=dict(payload))

    def delete_tag(self, tag_id: str) -> Any:
        """DELETE /tags/{id} — **soft-delete**. Scope `tags:write`.

        The tag is removed from all threads it was applied to.
        """
        return self._request("DELETE", f"/tags/{tag_id}")

    # --- tag groups ---------------------------------------------------------

    def list_tag_groups(self, limit: Optional[int] = None,
                        cursor: Optional[str] = None) -> Any:
        """GET /tags/groups — tag groups. Scope `tags:read`.

        An `id` returned here is the `tag_group_id` to pass to `create_tag`.
        """
        return self._list("/tags/groups", limit, cursor)

    def get_tag_group(self, group_id: str) -> Any:
        """GET /tags/groups/{id} — one tag group. Scope `tags:read`."""
        return self._request("GET", f"/tags/groups/{group_id}")

    def create_tag_group(self, name: str, color: str) -> Any:
        """POST /tags/groups — create a tag group. Scope `tags:write`.

        `name` and `color` are both required by upstream.
        """
        if not name or not color:
            raise ValueError("`name` and `color` are both required.")
        return self._request("POST", "/tags/groups",
                             json={"name": name, "color": color})

    def update_tag_group(self, group_id: str, payload: Dict[str, Any]) -> Any:
        """PATCH /tags/groups/{id} — update a group. Scope `tags:write`.

        Fields: `name`, `color`.
        """
        return self._request("PATCH", f"/tags/groups/{group_id}",
                             json=dict(payload))

    def delete_tag_group(self, group_id: str) -> Any:
        """DELETE /tags/groups/{id} — **soft-delete of an EMPTY group**.

        Scope `tags:write`. A group that still holds tags is refused:
        move them first (`update_tag(tag_group_id=…)`) or delete them.
        """
        return self._request("DELETE", f"/tags/groups/{group_id}")

    # --- reply snippets ------------------------------------------------------

    def list_snippets(self, limit: Optional[int] = None,
                      cursor: Optional[str] = None,
                      title_contains: Optional[str] = None,
                      folder_id: Optional[str] = None,
                      created_after: Optional[str] = None,
                      created_before: Optional[str] = None,
                      updated_after: Optional[str] = None,
                      updated_before: Optional[str] = None) -> Any:
        """GET /snippets — reusable reply snippets. **Pro plan required**.

        Scope `snippets:read`.
        """
        return self._list("/snippets", limit, cursor, {
            "title_contains": title_contains, "folder_id": folder_id,
            "created_after": created_after, "created_before": created_before,
            "updated_after": updated_after, "updated_before": updated_before,
        })

    def get_snippet(self, snippet_id: str) -> Any:
        """GET /snippets/{id} — one snippet. Scope `snippets:read`, Pro plan."""
        return self._request("GET", f"/snippets/{snippet_id}")

    def create_snippet(self, title: str, html: str,
                       folder_id: Optional[str] = None) -> Any:
        """POST /snippets — create a snippet. Scope `snippets:write`, Pro plan.

        ⚠️ The body is **HTML** (`html`), not markdown — unlike the
        `content` of a doc article.
        """
        if not title or not html:
            raise ValueError("`title` and `html` are both required.")
        body: Dict[str, Any] = {"title": title, "html": html}
        if folder_id is not None:
            body["folder_id"] = folder_id
        return self._request("POST", "/snippets", json=body)

    def update_snippet(self, snippet_id: str, payload: Dict[str, Any]) -> Any:
        """PATCH /snippets/{id} — update a snippet. Scope `snippets:write`.

        Fields: `title`, `html`, `folder_id`.
        """
        return self._request("PATCH", f"/snippets/{snippet_id}",
                             json=dict(payload))

    def delete_snippet(self, snippet_id: str) -> Any:
        """DELETE /snippets/{id} — **soft-delete**. Scope `snippets:write`."""
        return self._request("DELETE", f"/snippets/{snippet_id}")

    def list_snippet_folders(self, limit: Optional[int] = None,
                             cursor: Optional[str] = None) -> Any:
        """GET /snippets/folders — snippet folders. Scope `snippets:read`."""
        return self._list("/snippets/folders", limit, cursor)
