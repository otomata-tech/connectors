"""Productlane help center — articles, groups, and the draft queue.

This mixin is never instantiated on its own: it is composed into `ProductlaneClient`,
which provides the transport (`_request`, `_list`, `_check_choice`).

**Two write paths, and they do not serve the same purpose:**

- DIRECT writes (`create_article`, `update_article`, `delete_article`)
  apply immediately;
- the DRAFT (`create_draft` then `accept_draft` / `decline_draft`) proposes a
  change for review. `kind="edit"` modifies an existing article, `create`
  proposes a new one, `delete` proposes its removal.

⚠️ `accept_draft` can answer **`superseded` instead of `accepted`**: the
draft no longer applies cleanly (the article moved underneath it). That is an HTTP
success that applied nothing — read the returned status, not just the code.

⚠️ An article's `visibility` is not binary: `public`, `agent` (visible to
AI agents), `internal`, `unlisted`. `all` only exists as a list FILTER — an
article cannot "be" of visibility `all`, hence two distinct constants.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..const import (DOC_KIND_FILTERS, DOC_VISIBILITIES,
                     DOC_VISIBILITY_FILTERS, DRAFT_KINDS, DRAFT_STATUSES)


class _DocsMixin:
    """Help center articles, groups and drafts."""

    # --- articles -----------------------------------------------------------

    def list_articles(self, limit: Optional[int] = None,
                      cursor: Optional[str] = None,
                      group_id: Optional[str] = None,
                      published: Optional[Any] = None,
                      archived: Optional[Any] = None,
                      visibility: Optional[str] = None,
                      kind: Optional[str] = None,
                      title_contains: Optional[str] = None,
                      portal_instance_id: Optional[str] = None,
                      created_after: Optional[str] = None,
                      created_before: Optional[str] = None,
                      updated_after: Optional[str] = None,
                      updated_before: Optional[str] = None,
                      language: Optional[str] = None) -> Any:
        """GET /docs/articles — help center articles. Scope `docs:read`.

        `visibility` and `kind` accept `all` here, which is NOT a write
        value (see the module header).
        """
        self._check_choice("visibility", visibility, DOC_VISIBILITY_FILTERS)
        self._check_choice("kind", kind, DOC_KIND_FILTERS)
        return self._list("/docs/articles", limit, cursor, {
            "group_id": group_id, "published": published, "archived": archived,
            "visibility": visibility, "kind": kind,
            "title_contains": title_contains,
            "portal_instance_id": portal_instance_id,
            "created_after": created_after, "created_before": created_before,
            "updated_after": updated_after, "updated_before": updated_before,
            "language": language,
        })

    def get_article(self, article_id: str,
                    language: Optional[str] = None) -> Any:
        """GET /docs/articles/{id} — one article. Scope `docs:read`."""
        return self._request("GET", f"/docs/articles/{article_id}",
                             params={"language": language})

    def create_article(self, payload: Dict[str, Any]) -> Any:
        """POST /docs/articles — create an article. Scope `docs:write`.

        Required: `title`, `content`, `group_id`. Optional: `summary`,
        `portal_instance_id`, `published`, `visibility`, `icon`, `language`.

        `content` is **markdown** (headings, bullet lists, numbered lists...).
        `group_id` is required: an article is born in a group (see `list_groups`).
        """
        self._check_choice("visibility", payload.get("visibility"),
                           DOC_VISIBILITIES)
        return self._request("POST", "/docs/articles", json=dict(payload))

    def update_article(self, article_id: str, payload: Dict[str, Any]) -> Any:
        """PATCH /docs/articles/{id} — update an article. Scope `docs:write`.

        Fields: `title`, `content`, `allow_image_removal`, `summary`,
        `published`, `visibility`, `archived`, `show_on_home_page`, `group_id`,
        `portal_instance_id`, `icon`, `language`.

        ⚠️ `allow_image_removal` allows the content rewrite to DELETE
        images that no longer appear in it. Without it, they are kept — it is
        a vendor safeguard against loss through partial re-copying.
        """
        self._check_choice("visibility", payload.get("visibility"),
                           DOC_VISIBILITIES)
        return self._request("PATCH", f"/docs/articles/{article_id}",
                             json=dict(payload))

    def delete_article(self, article_id: str) -> Any:
        """DELETE /docs/articles/{id} — delete an article. Scope `docs:write`."""
        return self._request("DELETE", f"/docs/articles/{article_id}")

    def move_articles(self, article_ids: Any, group_id: Optional[str]) -> Any:
        """POST /docs/articles/move — reassign articles to a group.

        Scope `docs:write`. `group_id=None` **ungroups** them — it is a meaningful
        value, not an absence, so it is sent as-is.
        """
        if not article_ids:
            raise ValueError("`article_ids` is required: at least one article to move.")
        return self._request("POST", "/docs/articles/move",
                             json={"article_ids": list(article_ids),
                                   "group_id": group_id})

    # --- groups -------------------------------------------------------------

    def list_groups(self, portal_instance_id: Optional[str] = None) -> Any:
        """GET /docs/groups — article groups. Scope `docs:read`.

        ⚠️ **No pagination** on this endpoint: it returns everything at once.
        """
        return self._request("GET", "/docs/groups",
                             params={"portal_instance_id": portal_instance_id})

    def create_group(self, name: str,
                     portal_instance_id: Optional[str] = None) -> Any:
        """POST /docs/groups — create an article group. Scope `docs:write`."""
        if not name:
            raise ValueError("`name` is required.")
        body: Dict[str, Any] = {"name": name}
        if portal_instance_id is not None:
            body["portal_instance_id"] = portal_instance_id
        return self._request("POST", "/docs/groups", json=body)

    def update_group(self, group_id: str, payload: Dict[str, Any]) -> Any:
        """PATCH /docs/groups/{id} — update a group. Scope `docs:write`.

        Fields: `name`, `order`, `portal_instance_id`.
        """
        return self._request("PATCH", f"/docs/groups/{group_id}",
                             json=dict(payload))

    def delete_group(self, group_id: str) -> Any:
        """DELETE /docs/groups/{id} — delete a group. Scope `docs:write`.

        The articles it contained are NOT deleted: they are ungrouped.
        """
        return self._request("DELETE", f"/docs/groups/{group_id}")

    # --- drafts -------------------------------------------------------------

    def list_drafts(self, limit: Optional[int] = None,
                    cursor: Optional[str] = None,
                    kind: Optional[str] = None,
                    status: Optional[str] = None,
                    article_id: Optional[str] = None,
                    group_id: Optional[str] = None,
                    portal_instance_id: Optional[str] = None,
                    created_after: Optional[str] = None,
                    created_before: Optional[str] = None,
                    updated_after: Optional[str] = None,
                    updated_before: Optional[str] = None) -> Any:
        """GET /docs/drafts — drafts awaiting review. Scope `docs:read`.

        ⚠️ `status` takes DRAFT values here (`draft`, `open`,
        `accepted`, `rejected`, `superseded`) — nothing to do with a thread's
        `status` (`open`/`snoozed`/`done`), which has the same name elsewhere.
        """
        self._check_choice("kind", kind, DRAFT_KINDS)
        self._check_choice("status", status, DRAFT_STATUSES)
        return self._list("/docs/drafts", limit, cursor, {
            "kind": kind, "status": status, "article_id": article_id,
            "group_id": group_id, "portal_instance_id": portal_instance_id,
            "created_after": created_after, "created_before": created_before,
            "updated_after": updated_after, "updated_before": updated_before,
        })

    def get_draft(self, draft_id: str) -> Any:
        """GET /docs/drafts/{id} — one draft. Scope `docs:read`."""
        return self._request("GET", f"/docs/drafts/{draft_id}")

    def create_draft(self, payload: Dict[str, Any]) -> Any:
        """POST /docs/drafts — propose a change for review. Scope `docs:write`.

        Required: `kind` (`edit` | `create` | `delete`). Optional: `article_id`
        (required in practice for `edit`/`delete`), `title`, `content`,
        `allow_image_removal`, `group_id`, `submit_for_review`.
        """
        self._check_choice("kind", payload.get("kind"), DRAFT_KINDS)
        if not payload.get("kind"):
            raise ValueError(
                "`kind` is required: 'edit', 'create' or 'delete'.")
        return self._request("POST", "/docs/drafts", json=dict(payload))

    def accept_draft(self, draft_id: str) -> Any:
        """POST /docs/drafts/{id}/accept — apply the draft. Scope `docs:write`.

        `edit` writes a new version of the article, `create` materializes an
        article (unpublished), `delete` soft-deletes the article.

        ⚠️ **Can answer `superseded` instead of `accepted`** when the draft
        no longer applies cleanly: HTTP success, nothing applied. Read the
        returned status, not just the return code.
        """
        return self._request("POST", f"/docs/drafts/{draft_id}/accept")

    def decline_draft(self, draft_id: str) -> Any:
        """POST /docs/drafts/{id}/decline — reject the draft. Scope `docs:write`.

        The row is kept for audit, marked `REJECTED`, and leaves the open
        queue.
        """
        return self._request("POST", f"/docs/drafts/{draft_id}/decline")
