"""Productlane changelogs — release notes, their tags, their broadcast.

This mixin is never instantiated on its own: it is composed into `ProductlaneClient`,
which provides the transport (`_request`, `_list`).

⚠️ **`broadcast` is the only call in this whole client that writes to third parties.** It
sends an email to subscribed contacts and/or posts to the configured Slack
channels. There is **no cancel, no draft, no recall**: a send that has gone out
has gone out for good, to people who are not the key's user. It is
therefore handled separately — explicit signature rather than an opaque dict, and a local refusal
when no channel is requested.

**`published` and broadcasting are two distinct things**, and the vendor
spells it out: "This endpoint never toggles `published`". Publishing
(making it visible on the portal) is done via `update_changelog(published=True)`;
broadcasting (pushing to mailboxes) is done here. Mixing them up means
either publishing without notifying, or notifying about an invisible page.

**Translations**: `language` creates/updates a TRANSLATION ROW instead of
the base row. Non-translatable fields always apply to the base.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


class _ChangelogsMixin:
    """Changelogs, changelog tags, broadcast."""

    # --- changelogs ---------------------------------------------------------

    def list_changelogs(self, limit: Optional[int] = None,
                        cursor: Optional[str] = None,
                        published: Optional[Any] = None,
                        archived: Optional[Any] = None,
                        language: Optional[str] = None,
                        title_contains: Optional[str] = None,
                        tag_id: Optional[str] = None,
                        portal_instance_id: Optional[str] = None,
                        created_after: Optional[str] = None,
                        created_before: Optional[str] = None,
                        updated_after: Optional[str] = None,
                        updated_before: Optional[str] = None) -> Any:
        """GET /changelogs — workspace changelogs. Scope `changelogs:read`."""
        return self._list("/changelogs", limit, cursor, {
            "published": published, "archived": archived, "language": language,
            "title_contains": title_contains, "tag_id": tag_id,
            "portal_instance_id": portal_instance_id,
            "created_after": created_after, "created_before": created_before,
            "updated_after": updated_after, "updated_before": updated_before,
        })

    def get_changelog(self, changelog_id: str,
                      language: Optional[str] = None) -> Any:
        """GET /changelogs/{id} — one changelog. Scope `changelogs:read`.

        `language` serves the matching translation row rather than the base.
        """
        return self._request("GET", f"/changelogs/{changelog_id}",
                             params={"language": language})

    def create_changelog(self, payload: Dict[str, Any]) -> Any:
        """POST /changelogs — create a changelog. Scope `changelogs:write`.

        Required: `title`, `content`. Optional: `date`, `published`,
        `image_url`, `portal_instance_id`, `language`, `tag_ids`.

        `published=True` makes it visible on the portal — **it notifies
        no one**: broadcasting is a separate call (`broadcast_changelog`).
        `language` creates a TRANSLATION row instead of filling the base.
        """
        return self._request("POST", "/changelogs", json=dict(payload))

    def update_changelog(self, changelog_id: str,
                         payload: Dict[str, Any]) -> Any:
        """PATCH /changelogs/{id} — update a changelog. Scope `changelogs:write`.

        Fields: `title`, `content`, `date`, `published`, `archived`,
        `image_url`, `portal_instance_id`, `tag_ids`, `language`.

        This is **where** `published` is toggled, never in `broadcast_changelog`.
        `language` upserts a translation row; non-translatable fields
        always apply to the base row.
        """
        return self._request("PATCH", f"/changelogs/{changelog_id}",
                             json=dict(payload))

    def delete_changelog(self, changelog_id: str) -> Any:
        """DELETE /changelogs/{id} — **soft-delete**. Scope `changelogs:write`."""
        return self._request("DELETE", f"/changelogs/{changelog_id}")

    # --- broadcast ----------------------------------------------------------

    def broadcast_changelog(self, changelog_id: str,
                            email: Optional[bool] = None,
                            slack: Optional[bool] = None,
                            message: Optional[str] = None,
                            subject: Optional[str] = None,
                            sender_name: Optional[str] = None,
                            from_email: Optional[str] = None) -> Any:
        """POST /changelogs/{id}/broadcast — **send the changelog to third parties**.

        Scope `changelogs:write`, Pro plan or higher.

        ⚠️ **External and irreversible side effect.** `email=True` writes to
        SUBSCRIBED contacts (email integration required); `slack=True` posts to
        the configured Slack channels (Slack connected required). None of this can
        be recalled or cancelled.

        ⚠️ **Never touches `published`** (explicit vendor contract): so an
        unpublished changelog can be broadcast — recipients would get a
        link to a page that is not visible. Publishing is
        `update_changelog(published=True)`.

        Parameters are named one by one, not passed as a dict, precisely
        because a call that sends mail deserves to be readable at its
        call site.

        Args:
            changelog_id: the changelog to broadcast.
            email: write to subscribed contacts.
            slack: post to the configured Slack channels.
            message: accompanying text.
            subject: email subject.
            sender_name: displayed sender name.
            from_email: sending address.
        """
        if not email and not slack:
            raise ValueError(
                "broadcasting requires at least one channel: `email=True` and/or "
                "`slack=True`. Without a channel, upstream refuses — and a call that "
                "broadcasts nothing would be a misunderstanding anyway.")
        body: Dict[str, Any] = {}
        for key, value in (("email", email), ("slack", slack),
                           ("message", message), ("subject", subject),
                           ("sender_name", sender_name),
                           ("from_email", from_email)):
            if value is not None:
                body[key] = value
        return self._request("POST", f"/changelogs/{changelog_id}/broadcast",
                             json=body)

    # --- changelog tags ----------------------------------------------------

    def list_changelog_tags(self) -> Any:
        """GET /changelog-tags — tags attachable to a changelog.

        Scope `changelogs:read`. ⚠️ **No pagination** on this endpoint,
        unlike v2 lists: it returns everything at once.
        """
        return self._request("GET", "/changelog-tags")

    def create_changelog_tag(self, name: str, color: Optional[str] = None,
                             icon: Optional[str] = None) -> Any:
        """POST /changelog-tags — create a tag. Scope `changelogs:write`, Scale plan.

        Provide **at least** `color` or `icon`: with `color` alone, the UI
        renders a colored dot; with `icon` (a Lucide icon name), it renders
        the icon in that color.
        """
        if not name:
            raise ValueError("`name` is required.")
        if color is None and icon is None:
            raise ValueError(
                "provide at least `color` or `icon`: a tag with neither "
                "has no rendering.")
        body: Dict[str, Any] = {"name": name}
        if color is not None:
            body["color"] = color
        if icon is not None:
            body["icon"] = icon
        return self._request("POST", "/changelog-tags", json=body)

    def update_changelog_tag(self, tag_id: str,
                             payload: Dict[str, Any]) -> Any:
        """PATCH /changelog-tags/{id} — update a tag.

        Scope `changelogs:write`, Scale plan. Fields: `name`, `color`, `icon`.
        `icon=None` **removes** the icon and falls back to the colored dot — it is
        a meaningful value, not an absence, so it must be present
        in the dict to be taken into account.
        """
        return self._request("PATCH", f"/changelog-tags/{tag_id}",
                             json=dict(payload))

    def delete_changelog_tag(self, tag_id: str) -> Any:
        """DELETE /changelog-tags/{id} — **HARD delete**. Scale plan.

        Scope `changelogs:write`. ⚠️ Unlike the other deletions in this
        client (soft-delete), this one is permanent, and the tag is
        **detached from all changelogs** it was applied to.
        """
        return self._request("DELETE", f"/changelog-tags/{tag_id}")
