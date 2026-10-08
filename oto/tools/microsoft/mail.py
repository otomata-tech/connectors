"""Microsoft Graph client — the signed-in person's Outlook mailbox (delegated access,
scopes `scopes.MAIL`).

Covers what an agent needs to triage and answer mail: folders, search, read a
message and its attachments, write a draft (new or reply), send it, move or delete
a message. Nothing is sent without an explicit `send_draft`: every write produces
a draft first.

## Protocol facts that shape a caller

- **`$search` and `$filter`/`$orderby` exclude each other** on messages: a
  searched list comes back in Graph's own order (most recent first), and the
  `unread` filter cannot be combined with a `query` — put the condition in the
  KQL query instead, or list without a query.
- **Filtering and sorting together** on messages requires the sorted property
  to appear first in the filter (otherwise Graph refuses the request as an
  "InefficientFilter"): the unread filter is written `receivedDateTime ge … and
  isRead eq false` for that reason.
- **An attachment travels inline only up to 3 MB**: larger files need an
  upload session, not covered here — `create_draft` refuses them.
- **`move` returns the message with a NEW id**: the old one no longer exists.
- Folder arguments accept a folder id or a well-known name (`inbox`,
  `archive`, `deleteditems`, `drafts`, `sentitems`, `junkemail`).
"""
from __future__ import annotations

import base64
import html
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional

from ..common.credentials import require
from ._transport import GraphBase, segment

#: Graph's ceiling for an attachment sent in the body of a request.
MAX_INLINE_ATTACHMENT = 3 * 1024 * 1024
BODY_TYPES = ("text", "html")
# The fields of a list: everything but the body, which `get_message` reads.
_LIST_FIELDS = ("id,subject,from,toRecipients,ccRecipients,receivedDateTime,sentDateTime,"
                "isRead,isDraft,hasAttachments,importance,bodyPreview,conversationId,"
                "parentFolderId,webLink")
_ATTACHMENT_FIELDS = "id,name,contentType,size,isInline,lastModifiedDateTime"
_BODY_TAG = re.compile(r"<body[^>]*>", re.IGNORECASE)


def _recipients(addresses: Iterable[str], name: str) -> List[Dict[str, Any]]:
    if isinstance(addresses, str):
        raise TypeError(f"{name} is a list of addresses, not a string")
    return [{"emailAddress": {"address": require(a, name)}} for a in addresses]


def _attachment(spec: Mapping[str, Any]) -> Dict[str, Any]:
    """`{name, content_type, content_bytes}` (raw bytes) → a Graph fileAttachment."""
    name = require(spec.get("name"), "attachment name")
    content = spec.get("content_bytes")
    if not isinstance(content, (bytes, bytearray)):
        raise TypeError(f"attachment {name!r}: content_bytes must be the raw bytes")
    if len(content) > MAX_INLINE_ATTACHMENT:
        raise ValueError(
            f"attachment {name!r} is {len(content)} bytes: Graph accepts at most "
            f"{MAX_INLINE_ATTACHMENT} bytes per attachment sent inline")
    return {"@odata.type": "#microsoft.graph.fileAttachment", "name": name,
            "contentType": spec.get("content_type") or "application/octet-stream",
            "contentBytes": base64.b64encode(bytes(content)).decode("ascii")}


def _above_quote(body_html: str, draft_body: Mapping[str, Any]) -> str:
    """The new text above the quoted message that Graph put in a reply draft."""
    quoted = str(draft_body.get("content") or "")
    if str(draft_body.get("contentType") or "").lower() != "html":
        quoted = f'<div style="white-space:pre-wrap">{html.escape(quoted)}</div>'
    match = _BODY_TAG.search(quoted)
    if match:
        return quoted[:match.end()] + body_html + quoted[match.end():]
    return body_html + quoted


class MailClient(GraphBase):
    """The signed-in person's Outlook mailbox."""

    @staticmethod
    def _message(message_id: str) -> str:
        return f"/me/messages/{segment(message_id, 'message_id')}"

    # ================================================================
    # Reading
    # ================================================================

    def list_folders(self, *, limit: int = 500) -> List[Dict[str, Any]]:
        """GET /me/mailFolders — the top-level folders (`id`, `displayName`,
        counts of items and unread items)."""
        return self._paged("/me/mailFolders", limit=limit)

    def search_messages(self, query: Optional[str] = None, *, folder: Optional[str] = None,
                        top: int = 25, unread: Optional[bool] = None
                        ) -> List[Dict[str, Any]]:
        """GET /me/messages (or /me/mailFolders/{folder}/messages) — at most `top`
        messages, without their body (`bodyPreview` only).

        Args:
            query: a KQL search (`from:jane subject:invoice`, plain words…), sent as
                `$search`; without it, the most recent messages first.
            folder: a folder id or a well-known name; every folder when omitted.
            unread: `True` / `False` to keep only unread / read messages. Not
                combinable with `query` (Graph refuses `$filter` with `$search`).
        """
        path = (f"/me/mailFolders/{segment(folder, 'folder')}/messages" if folder
                else "/me/messages")
        params: Dict[str, Any] = {"$select": _LIST_FIELDS}
        if query is not None:
            if unread is not None:
                raise ValueError("unread cannot be combined with query: Graph refuses "
                                 "$filter with $search on messages")
            params["$search"] = '"' + require(query, "query").replace('"', '\\"') + '"'
        else:
            params["$orderby"] = "receivedDateTime desc"
            if unread is not None:
                params["$filter"] = (f"receivedDateTime ge 1900-01-01T00:00:00Z and "
                                     f"isRead eq {'false' if unread else 'true'}")
        return self._paged(path, params, limit=top)

    def get_message(self, message_id: str, *, body: str = "text") -> Dict[str, Any]:
        """GET /me/messages/{id} — the whole message, its body as `text` (default,
        what an agent reads) or `html`."""
        if body not in BODY_TYPES:
            raise ValueError(f"body must be one of {', '.join(BODY_TYPES)}")
        return self._json("GET", self._message(message_id),
                          headers={"Prefer": f'outlook.body-content-type="{body}"'})

    def list_attachments(self, message_id: str) -> List[Dict[str, Any]]:
        """GET /me/messages/{id}/attachments — name, type and size of each
        attachment, without its content (see `get_attachment`)."""
        return self._paged(f"{self._message(message_id)}/attachments",
                           {"$select": _ATTACHMENT_FIELDS}, limit=500, page_size=None)

    def get_attachment(self, message_id: str, attachment_id: str) -> Dict[str, Any]:
        """GET /me/messages/{id}/attachments/{id} — one attachment; a file's content
        is in `contentBytes`, base64-encoded."""
        return self._json("GET", f"{self._message(message_id)}/attachments/"
                                 f"{segment(attachment_id, 'attachment_id')}")

    # ================================================================
    # Writing — drafts first, sending is a separate step
    # ================================================================

    def create_draft(self, *, to: Iterable[str], subject: str, body_html: str,
                     cc: Iterable[str] = (), bcc: Iterable[str] = (),
                     attachments: Iterable[Mapping[str, Any]] = ()) -> Dict[str, Any]:
        """POST /me/messages — a new draft in the Drafts folder, not sent.

        Args:
            to / cc / bcc: email addresses.
            attachments: `{"name", "content_type", "content_bytes"}` each, the
                content as raw bytes, ≤ 3 MB each (refused beyond, before anything
                is written). Each one is added to the draft by its own request,
                so that their sum is not bound by Graph's request size.

        Returns the draft as created (its `id` is the one `send_draft` takes).
        """
        files = [_attachment(a) for a in attachments]
        draft = self._json("POST", "/me/messages", json={
            "subject": subject or "",
            "body": {"contentType": "HTML", "content": body_html or ""},
            "toRecipients": _recipients(to, "to"),
            "ccRecipients": _recipients(cc, "cc"),
            "bccRecipients": _recipients(bcc, "bcc"),
        })
        for file in files:
            self._json("POST", f"{self._message(draft['id'])}/attachments", json=file)
        return draft

    def create_reply_draft(self, message_id: str, *, body_html: str,
                           reply_all: bool = False) -> Dict[str, Any]:
        """POST /me/messages/{id}/createReply (or createReplyAll), then PATCH of
        the draft's body — a reply draft, not sent, recipients and subject set by
        Graph, `body_html` placed above the quoted message.

        Returns the updated draft."""
        action = "createReplyAll" if reply_all else "createReply"
        draft = self._json("POST", f"{self._message(message_id)}/{action}")
        return self._json("PATCH", self._message(draft["id"]), json={
            "body": {"contentType": "HTML",
                     "content": _above_quote(body_html or "", draft.get("body") or {})}})

    def send_draft(self, message_id: str) -> str:
        """POST /me/messages/{id}/send — sends a draft (it moves to Sent Items).
        Returns the id passed in."""
        self._json("POST", f"{self._message(message_id)}/send")
        return message_id

    def move(self, message_id: str, destination: str) -> Dict[str, Any]:
        """POST /me/messages/{id}/move — to a folder id or a well-known name
        (`archive`, `deleteditems`, `inbox`…). ⚠️ Returns the message with its NEW id."""
        return self._json("POST", f"{self._message(message_id)}/move",
                          json={"destinationId": require(destination, "destination")})

    def delete(self, message_id: str) -> str:
        """DELETE /me/messages/{id} — removes the message from its folder; the
        mailbox keeps it as recoverable for its retention period. To put it in the
        trash folder instead, `move(message_id, "deleteditems")`. Returns the id."""
        self._json("DELETE", self._message(message_id))
        return message_id
