"""Microsoft Graph client — Microsoft Teams on behalf of the signed-in person
(delegated access): their teams and channels, channel messages and replies, their
chats.

## Protocol facts that shape a caller

- **Two consent levels.** `scopes.TEAMS` (teams, channels, posting in a channel,
  chats) is consented by the person; READING channel messages
  (`list_channel_messages`, `list_replies`) needs `scopes.TEAMS_ADMIN`, which
  only a tenant administrator can grant (`auth.admin_consent_url`). Without it,
  those two calls fail with a 403.
- **Pages of messages and chats are capped at 50** (`$top`); `limit` beyond that
  follows `@odata.nextLink`. Teams and channels are returned in one go: those
  endpoints refuse `$top`.
- **A post is a message to other people**, visible at once to the channel or
  chat members; Teams has no draft.
- Ids carry `:` and `@` (`19:…@thread.tacv2`): pass them as returned.
"""
from __future__ import annotations

from typing import Any, Dict, List

from ..common.credentials import require
from ._transport import GraphBase, segment

#: Graph's `$top` ceiling on channel messages, replies, chats and chat messages.
TEAMS_PAGE_SIZE = 50
# Teams and channels come back whole; this only bounds what is kept.
_UNPAGED_LIMIT = 1000


def _html(content: str) -> Dict[str, Any]:
    return {"body": {"contentType": "html", "content": require(content, "html")}}


class TeamsClient(GraphBase):
    """Microsoft Teams for the signed-in person."""

    @staticmethod
    def _channel(team_id: str, channel_id: str) -> str:
        return (f"/teams/{segment(team_id, 'team_id')}"
                f"/channels/{segment(channel_id, 'channel_id')}")

    @staticmethod
    def _chat(chat_id: str) -> str:
        return f"/chats/{segment(chat_id, 'chat_id')}"

    # ================================================================
    # Teams and channels
    # ================================================================

    def list_joined_teams(self) -> List[Dict[str, Any]]:
        """GET /me/joinedTeams — the teams the person is a member of."""
        return self._paged("/me/joinedTeams", limit=_UNPAGED_LIMIT, page_size=None)

    def list_channels(self, team_id: str) -> List[Dict[str, Any]]:
        """GET /teams/{id}/channels — the channels of a team the person can see."""
        return self._paged(f"/teams/{segment(team_id, 'team_id')}/channels",
                           limit=_UNPAGED_LIMIT, page_size=None)

    def list_channel_messages(self, team_id: str, channel_id: str, *, limit: int = 50
                              ) -> List[Dict[str, Any]]:
        """GET /teams/{t}/channels/{c}/messages — the channel's root messages,
        without their replies. Needs `scopes.TEAMS_ADMIN`."""
        return self._paged(f"{self._channel(team_id, channel_id)}/messages",
                           limit=limit, page_size=TEAMS_PAGE_SIZE)

    def list_replies(self, team_id: str, channel_id: str, message_id: str, *,
                     limit: int = 50) -> List[Dict[str, Any]]:
        """GET …/messages/{id}/replies — the replies to one channel message. Needs
        `scopes.TEAMS_ADMIN`."""
        return self._paged(
            f"{self._channel(team_id, channel_id)}/messages/"
            f"{segment(message_id, 'message_id')}/replies",
            limit=limit, page_size=TEAMS_PAGE_SIZE)

    def post_channel_message(self, team_id: str, channel_id: str, *, html: str
                             ) -> Dict[str, Any]:
        """POST /teams/{t}/channels/{c}/messages — a new thread in the channel.
        Returns the posted message."""
        return self._json("POST", f"{self._channel(team_id, channel_id)}/messages",
                          json=_html(html))

    def reply_channel_message(self, team_id: str, channel_id: str, message_id: str, *,
                              html: str) -> Dict[str, Any]:
        """POST …/messages/{id}/replies — a reply in a channel thread. Returns the
        posted reply."""
        return self._json(
            "POST", f"{self._channel(team_id, channel_id)}/messages/"
                    f"{segment(message_id, 'message_id')}/replies",
            json=_html(html))

    # ================================================================
    # Chats
    # ================================================================

    def list_chats(self, *, limit: int = 50) -> List[Dict[str, Any]]:
        """GET /me/chats — the person's one-on-one, group and meeting chats."""
        return self._paged("/me/chats", limit=limit, page_size=TEAMS_PAGE_SIZE)

    def list_chat_messages(self, chat_id: str, *, limit: int = 50) -> List[Dict[str, Any]]:
        """GET /chats/{id}/messages — the messages of a chat."""
        return self._paged(f"{self._chat(chat_id)}/messages", limit=limit,
                           page_size=TEAMS_PAGE_SIZE)

    def post_chat_message(self, chat_id: str, *, html: str) -> Dict[str, Any]:
        """POST /chats/{id}/messages — a message in the chat. Returns it."""
        return self._json("POST", f"{self._chat(chat_id)}/messages", json=_html(html))
