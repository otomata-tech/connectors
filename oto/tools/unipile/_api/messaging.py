"""Messaging: inboxes, threads, messages, participants, thread state.

Extracted from `client.py` (split by domain, frozen public surface):
the bodies are unchanged. This mixin is never instantiated on its own — it is
composed into `UnipileClient`, which provides the transport (`_request`,
`_acct`, `_norm`, `_by_shape`, `session`).
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import quote

from ..errors import UnipileError

logger = logging.getLogger(__name__)


class _MessagingMixin:
    """Messaging: inboxes, threads, messages, participants, thread state."""

    def list_inboxes(self) -> dict:
        """Account inboxes (v2: `GET /v2/{account}/inboxes`). LinkedIn classic:
        `CLASSIC_PRIMARY` (main), `CLASSIC_ARCHIVED`, `CLASSIC_SPAM`,
        `CLASSIC_JOBS`, `CLASSIC_INMAIL`, `CLASSIC_STARRED`."""
        return self._norm(self._request("GET", self._acct("/inboxes")))

    def list_chats(self, limit: int = 20, cursor: Optional[str] = None,
                   with_attendee_names: bool = False,
                   inbox: str = "CLASSIC_PRIMARY") -> dict:
        """Messaging threads, in the provider's endpoint shape (`_by_shape`):
        **per inbox** for LinkedIn (`GET /v2/{account}/inboxes/{inbox}/chats` —
        the old `/chats` returns 501 "Use List inbox Chats endpoint" there, live delta
        2026-07-06), **flat** for providers without an inbox (WhatsApp, Telegram,
        Instagram, Messenger, Twitter), where it's the inbox shape that returns 501.
        `inbox` (LinkedIn) defaults to `CLASSIC_PRIMARY`; others via `list_inboxes`."""
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        data = self._norm(self._by_shape(
            lambda: self._request(
                "GET", self._acct(f"/inboxes/{quote(inbox, safe='')}/chats"),
                params=params),
            lambda: self._request("GET", self._acct("/chats"), params=params),
            "list_chats"))
        if with_attendee_names:
            self._annotate_chat_attendees(data)
        return data

    def resolve_attendee_names(self, provider_ids, max_pages: int = 10,
                               page_limit: int = 100) -> dict:
        """Resolve `attendee_provider_id`s via the v2 contacts book
        (`/v2/{account}/contacts`, paginated). Best-effort."""
        wanted = {str(p) for p in provider_ids if p}
        out: dict[str, dict] = {}
        cursor = None
        for _ in range(max_pages):
            if not wanted - out.keys():
                break
            page = self.list_attendees(cursor=cursor, limit=page_limit)
            items = (page or {}).get("items") or []
            for att in items:
                if not isinstance(att, dict):
                    continue
                pid = str(att.get("provider_id") or att.get("id") or "")
                if pid in wanted:
                    out[pid] = att
            cursor = (page or {}).get("cursor")
            if not items or not cursor:
                break
        return out

    def _annotate_chat_attendees(self, data: Any) -> None:
        """Enrich the threads of a `/chats` in place with the counterpart's name.

        Best-effort: never raises (the list takes precedence over the enrichment). But
        best-effort doesn't mean SILENT — the caller asked for this enrichment, and
        the tool's description promises it; if it didn't happen they must
        learn it from the response, not deduce it from an absence.

        The case that cost us (signal #682, 03/09/2026): on ~40 threads, the agent concluded
        that "the enrichment announced in the doc doesn't show up", unable to
        tell a resolution failure from an incomplete contacts book. The
        log, for its part, already carried the warning — server-side, where the caller never
        reads it.

        The worst of the four silences is not the failure, it's PARTIAL resolution:
        it returns a heterogeneous list where the absence of `attendee_name` reads "this thread
        has no counterpart" instead of "I couldn't name them".

        Only reported on a gap: everything resolved ⟹ nothing to announce, the caller sees the
        names — and a page of threads already weighs ~105 KB, we don't add noise."""
        items = (data or {}).get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return

        def _dire(status: str, **kw) -> None:
            if isinstance(data, dict):
                data["attendee_names"] = {"status": status, **kw}

        ids = {str(it.get("attendee_provider_id"))
               for it in items
               if isinstance(it, dict) and it.get("attendee_provider_id")}
        if not ids:
            if items:
                _dire("unavailable", asked=0, resolved=0,
                      reason="no thread carries an `attendee_provider_id`: nothing to "
                             "resolve. The counterpart's name remains readable in "
                             "`name` (1-to-1 threads) or via `last_message.sender`.")
            return
        try:
            resolved = self.resolve_attendee_names(ids)
        except Exception as e:  # noqa: BLE001 — best-effort enrichment intended
            logger.warning("unipile chats: attendees resolution failed, "
                           "list served without enrichment", exc_info=True)
            _dire("unavailable", asked=len(ids), resolved=0,
                  reason=f"contact resolution failed ({type(e).__name__}): "
                         "the threads are served WITHOUT `attendee_name`. This is not "
                         "the absence of a counterpart — fall back on `name` or "
                         "`last_message.sender`, or replay the call.")
            return
        manquants = []
        for it in items:
            if not isinstance(it, dict):
                continue
            pid = str(it.get("attendee_provider_id") or "")
            att = resolved.get(pid)
            if not att:
                if pid:
                    manquants.append(pid)
                continue
            it["attendee_name"] = att.get("name")
            it["attendee_headline"] = (att.get("specifics") or {}).get("occupation")
            it["attendee_profile_url"] = att.get("profile_url")
        if manquants:
            _dire("partial", asked=len(ids), resolved=len(ids) - len(set(manquants)),
                  missing_ids=sorted(set(manquants))[:20],
                  reason="these counterparts are missing from the contacts book: their "
                         "threads have NO `attendee_name`, which doesn't mean "
                         "they have no counterpart. Name them by "
                         "`last_message.sender`.")

    def list_messages(self, chat_id: str, limit: int = 50) -> dict:
        params = {"limit": limit}
        return self._norm(self._request(
            "GET", self._acct(f"/chats/{quote(chat_id, safe='')}/messages"),
            params=params,
        ))

    def send_message(
        self,
        text: str,
        chat_id: Optional[str] = None,
        attendee_id: Optional[str] = None,
        inbox: str = "CLASSIC_PRIMARY",
    ) -> dict:
        if chat_id:
            return self._request(
                "POST", self._acct(f"/chats/{quote(chat_id, safe='')}/messages/send"),
                json={"text": text},
            )
        if not attendee_id:
            raise UnipileError("send_message: chat_id or attendee_id required.")
        # v2: for an INBOX provider (LinkedIn), the new thread goes through the inbox —
        # `POST /v2/{account}/inboxes/{inbox}/chats/send`. The generic `/chats/send` returns
        # 501 "Use Start a Chat in the given inbox endpoint for this provider" there
        # (seen live 2026-07-08 — same inbox model as list_chats, signal #199/#200);
        # without an inbox (WhatsApp & co.), it's the reverse. Same body on both sides
        # (`users_ids`, which replaces v1's `attendees_ids`): only the route changes.
        body = {"users_ids": [attendee_id], "text": text}
        return self._by_shape(
            lambda: self._request(
                "POST", self._acct(f"/inboxes/{quote(inbox, safe='')}/chats/send"),
                json=body),
            lambda: self._request("POST", self._acct("/chats/send"), json=body),
            "send_message",
        )

    def list_chat_attendees(self, chat_id: str) -> dict:
        """Participants of a thread. v2: `/chats/{chat_id}/participants`."""
        return self._norm(self._request(
            "GET", self._acct(f"/chats/{quote(chat_id, safe='')}/participants")
        ))

    def list_attendees(self, cursor: Optional[str] = None,
                      limit: Optional[int] = None) -> dict:
        """Contacts book. v2: `/v2/{account}/contacts`."""
        params: dict[str, Any] = {}
        if cursor:
            params["cursor"] = cursor
        if limit:
            params["limit"] = limit
        return self._norm(self._request(
            "GET", self._acct("/contacts"), params=params
        ))

    # v2 updateChat: dedicated fields (no more {action, value} pair).
    _CHAT_ACTION_FIELD = {
        "setReadStatus": "read_status",
        "setMuteStatus": "muted_until",
        "setArchiveStatus": "archive_status",
        "setPinnedStatus": "pin_status",
        "setLabel": "label",
    }

    def patch_chat(self, chat_id: str, action: str, value: Any = None) -> dict:
        """Modify a thread's state (`PATCH /chats/{id}`)."""
        field = self._CHAT_ACTION_FIELD.get(action)
        if field is None:
            raise UnipileError(
                f"patch_chat: action {action!r} not supported "
                f"({', '.join(self._CHAT_ACTION_FIELD)})."
            )
        return self._request(
            "PATCH", self._acct(f"/chats/{quote(chat_id, safe='')}"),
            json={field: value},
        )

    def react_message(self, message_id: str, reaction: str,
                      chat_id: Optional[str] = None) -> dict:
        """React to a message. v2 requires the `chat_id` (route under the thread)."""
        if not chat_id:
            raise UnipileError(
                "react_message: chat_id required "
                "(route /chats/{chat_id}/messages/{message_id}/reactions)."
            )
        return self._request(
            "POST",
            self._acct(
                f"/chats/{quote(chat_id, safe='')}"
                f"/messages/{quote(message_id, safe='')}/reactions"
            ),
            json={"reaction": reaction},
        )
