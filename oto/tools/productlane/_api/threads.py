"""Productlane threads — the customer feedback inbox, its messages and comments.

This mixin is never instantiated on its own: it is composed into `ProductlaneClient`,
which provides the transport (`_request`, `_list`, `_check_choice`, `_check_limit`).

Two planes not to be confused, because one is PUBLIC and the other is not:

- a **message** (`/threads/{id}/messages`) goes to the contact, through the channel
  the thread came from (email, Slack, live chat, Teams) — it is a real
  outbound communication;
- an **internal comment** (`/threads/{id}/comments`) is visible only to the
  team.

Both are written with a `content` field and `attachments`: nothing in the
shape of the call tells you which one leaves the organization. That is why the
two methods carry explicit names (`send_message` / `post_comment`) and
why the first restates it in its docstring.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..const import (MESSAGE_DIRECTIONS, MESSAGE_ORDERS, MESSAGE_TYPES,
                     PAIN_LEVELS, THREAD_EXPANDS, THREAD_ORIGINS,
                     THREAD_STATUSES, THREAD_TABS)


class _ThreadsMixin:
    """Threads, messages, internal comments and Linear links."""

    # --- threads ------------------------------------------------------------

    def list_threads(self, limit: Optional[int] = None,
                     cursor: Optional[str] = None,
                     status: Optional[str] = None, tab: Optional[str] = None,
                     assignee_id: Optional[str] = None,
                     contact_id: Optional[str] = None,
                     company_id: Optional[str] = None,
                     tag_id: Optional[str] = None,
                     pain_level: Optional[str] = None,
                     origin: Optional[str] = None,
                     issue_id: Optional[str] = None,
                     project_id: Optional[str] = None,
                     external_id: Optional[str] = None,
                     created_after: Optional[str] = None,
                     created_before: Optional[str] = None,
                     updated_after: Optional[str] = None,
                     updated_before: Optional[str] = None) -> Any:
        """GET /threads — workspace threads. Scope `threads:read`.

        Cursor-paginated (`page.cursor` / `page.has_more`), sorted
        `created_at DESC` with no way to change the order server-side.
        """
        self._check_choice("status", status, THREAD_STATUSES)
        self._check_choice("tab", tab, THREAD_TABS)
        self._check_choice("pain_level", pain_level, PAIN_LEVELS)
        self._check_choice("origin", origin, THREAD_ORIGINS)
        return self._list("/threads", limit, cursor, {
            "status": status, "tab": tab, "assignee_id": assignee_id,
            "contact_id": contact_id, "company_id": company_id,
            "tag_id": tag_id, "pain_level": pain_level, "origin": origin,
            "issue_id": issue_id, "project_id": project_id,
            "external_id": external_id,
            "created_after": created_after, "created_before": created_before,
            "updated_after": updated_after, "updated_before": updated_before,
        })

    def get_thread(self, thread_id: str, expand: Optional[Any] = None) -> Any:
        """GET /threads/{id} — one thread. Scope `threads:read`.

        `expand` inlines related resources: `messages`, `comments`, or
        both. Accepts a list or a comma-separated string.

        ⚠️ **Upstream IGNORES an unknown `expand` value** instead of
        rejecting it: a typo would return a thread without its messages, with no
        word of explanation. The values are therefore checked here.
        """
        if expand is not None:
            values = expand.split(",") if isinstance(expand, str) else list(expand)
            values = [v.strip() for v in values if str(v).strip()]
            for v in values:
                self._check_choice("expand", v, THREAD_EXPANDS)
            expand = ",".join(values) or None
        return self._request("GET", f"/threads/{thread_id}",
                             params={"expand": expand})

    def create_thread(self, payload: Dict[str, Any]) -> Any:
        """POST /threads — create a thread and **upsert its contact by email**.

        Scope `threads:write`. Required: `text`, `pain_level`, `contact_email`.
        Optional: `external_ids`, `title`, `status`, `origin`, `contact_name`,
        `assignee_id`, `company_id`, `project_id`, `issue_id`, `created_at`,
        `updated_at`, `notify`.

        ⚠️ The contact is **created if it does not exist**: this call therefore writes to
        two tables, and `contact_email` is more than a pointer.
        ⚠️ `notify` triggers an outbound notification — leaving it out is
        the quiet behavior.
        """
        self._check_choice("pain_level", payload.get("pain_level"), PAIN_LEVELS)
        self._check_choice("status", payload.get("status"), THREAD_STATUSES)
        self._check_choice("origin", payload.get("origin"), THREAD_ORIGINS)
        return self._request("POST", "/threads", json=dict(payload))

    def update_thread(self, thread_id: str, payload: Dict[str, Any]) -> Any:
        """PATCH /threads/{id} — update a thread. Scope `threads:write`.

        Fields: `external_ids`, `text`, `title`, `pain_level`, `assignee_id`,
        `contact_id`, `company_id`, `tag_ids`, `project_id`, `status`,
        `snoozed_until`, `closed_loop`, `ai_draft_html`, `ai_draft_sources`,
        `clear_ai_draft_error`, `notify`.

        ⚠️ `tag_ids` REPLACES the list of tags, it does not add to it.
        """
        self._check_choice("pain_level", payload.get("pain_level"), PAIN_LEVELS)
        self._check_choice("status", payload.get("status"), THREAD_STATUSES)
        return self._request("PATCH", f"/threads/{thread_id}", json=dict(payload))

    def delete_thread(self, thread_id: str) -> Any:
        """DELETE /threads/{id} — **soft-delete** a thread. Scope `threads:write`."""
        return self._request("DELETE", f"/threads/{thread_id}")

    # --- messages (OUTBOUND) -----------------------------------------------

    def list_messages(self, thread_id: str, limit: Optional[int] = None,
                      cursor: Optional[str] = None, order: Optional[str] = None,
                      type: Optional[str] = None,
                      direction: Optional[str] = None,
                      user_id: Optional[str] = None, q: Optional[str] = None,
                      created_after: Optional[str] = None,
                      created_before: Optional[str] = None,
                      updated_after: Optional[str] = None,
                      updated_before: Optional[str] = None) -> Any:
        """GET /threads/{id}/messages — the conversation, **all channels merged**.

        Scope `threads:read`. Sorted oldest to newest by default
        (`order="asc"`), unlike the other v2 lists which are
        `created_at DESC`: it is a conversation, it reads in order.
        """
        self._check_choice("order", order, MESSAGE_ORDERS)
        self._check_choice("type", type, MESSAGE_TYPES)
        self._check_choice("direction", direction, MESSAGE_DIRECTIONS)
        return self._list(f"/threads/{thread_id}/messages", limit, cursor, {
            "order": order, "type": type, "direction": direction,
            "user_id": user_id, "q": q,
            "created_after": created_after, "created_before": created_before,
            "updated_after": updated_after, "updated_before": updated_before,
        })

    def send_message(self, thread_id: str, payload: Dict[str, Any]) -> Any:
        """POST /threads/{id}/messages — **send a message to the contact**.

        Scope `threads:write`. Required: `content`. Optional: `cc`, `bcc`,
        `attachments`, `channel_id`, `author`.

        ⚠️ **Real outbound communication**: the channel (email, Slack, live chat,
        Microsoft Teams) is inferred from the thread's origin, and the response says which
        one was used. For a note that stays within the team, use `post_comment`.

        A **400 `validation_failed`** signals that the integration matching the
        inferred channel is not configured for the workspace — so it is not
        a defect in the content sent.
        """
        return self._request("POST", f"/threads/{thread_id}/messages",
                             json=dict(payload))

    # --- internal comments -------------------------------------------------

    def list_comments(self, thread_id: str, limit: Optional[int] = None,
                      cursor: Optional[str] = None) -> Any:
        """GET /threads/{id}/comments — internal comments. Scope `threads:read`.

        Visible to the team only.
        """
        return self._list(f"/threads/{thread_id}/comments", limit, cursor)

    def post_comment(self, thread_id: str, content: str,
                     attachments: Optional[Any] = None) -> Any:
        """POST /threads/{id}/comments — **internal** comment. Scope `comments:write`.

        Visible to teammates only: nothing goes to the contact. It is the
        quiet counterpart of `send_message`.
        """
        body: Dict[str, Any] = {"content": content}
        if attachments is not None:
            body["attachments"] = attachments
        return self._request("POST", f"/threads/{thread_id}/comments", json=body)

    def update_comment(self, thread_id: str, comment_id: str,
                       payload: Dict[str, Any]) -> Any:
        """PATCH /threads/{id}/comments/{comment_id} — edit an internal comment.

        Scope `comments:write`. Fields: `content`, `attachments`.
        """
        return self._request("PATCH",
                             f"/threads/{thread_id}/comments/{comment_id}",
                             json=dict(payload))

    def delete_comment(self, thread_id: str, comment_id: str) -> Any:
        """DELETE /threads/{id}/comments/{comment_id} — **soft-delete**.

        Scope `comments:write`.
        """
        return self._request("DELETE",
                             f"/threads/{thread_id}/comments/{comment_id}")

    # --- link to Linear -----------------------------------------------------

    def link_thread(self, thread_id: str,
                    issue_ids: Optional[Any] = None,
                    project_ids: Optional[Any] = None,
                    priority: Optional[Any] = None) -> Any:
        """POST /threads/{id}/customer-needs — link the thread to issues/projects.

        Scope `threads:write`. Goes through Linear's "customer need" pipeline,
        **which must therefore be connected**: without Linear, upstream refuses.

        This is the gesture that turns customer feedback into a tracked request on the
        roadmap — and what gives a Productlane project a "score".
        """
        body: Dict[str, Any] = {}
        if issue_ids is not None:
            body["issue_ids"] = issue_ids
        if project_ids is not None:
            body["project_ids"] = project_ids
        if priority is not None:
            body["priority"] = priority
        if not body:
            raise ValueError(
                "linking a thread requires at least `issue_ids` or `project_ids`.")
        return self._request("POST", f"/threads/{thread_id}/customer-needs",
                             json=body)
