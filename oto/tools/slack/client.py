"""
Slack API Client.

Requires: requests
"""

import hmac
import hashlib
from typing import Optional, Dict, Any, List

import requests

from ..common.credentials import MissingCredential
from .text import MAX_TEXT_LEN as _MAX_TEXT_LEN
from .text import chunk_text as _chunk_text
from .text import escape_false_emoji_shortcodes as _escape_false_emoji_shortcodes

_HTTP_TIMEOUT = (10, 60)  # (connect, read) — never an unbounded wait


# Slack answers HTTP 200 with `{"ok": false, "error": "<code>"}` for logical
# rejections (raise_for_status does not see them). We translate them into a TYPED
# upstream error carrying `.status` (same contract as the other oto-core connectors, e.g.
# NinjaError): a client rejection code (channel not found, permissions, scope…) is
# an upstream 4xx — not a backend bug — so triaged as such downstream (calllog,
# Sentry). Real Slack incidents (internal_error…) stay 5xx → reported.
_SLACK_ERROR_STATUS = {
    # 401 — authentification
    "not_authed": 401, "invalid_auth": 401, "account_inactive": 401,
    "token_revoked": 401, "token_expired": 401,
    # 403 — authorization / scope
    "missing_scope": 403, "not_allowed_token_type": 403, "ekm_access_denied": 403,
    "not_in_channel": 403, "is_archived": 403, "restricted_action": 403,
    "cant_post_message": 403, "no_permission": 403,
    # 404 — target missing
    "channel_not_found": 404, "user_not_found": 404, "users_not_found": 404,
    "message_not_found": 404, "thread_not_found": 404, "file_not_found": 404,
    # 429 — quota
    "ratelimited": 429, "rate_limited": 429,
    # 5xx — incident on Slack's side (to report, real upstream bug)
    "internal_error": 502, "fatal_error": 502, "service_unavailable": 503,
    "request_timeout": 504,
}


class SlackError(RuntimeError):
    """Slack API error (`ok:false`). `status` = HTTP equivalent of the Slack code
    (default 400 = client rejection), `error` = the raw Slack code.

    `needed` / `provided`: on a `missing_scope`, Slack ITSELF NAMES the missing
    right and those it saw on the token. Probed on 2026-08-28 —
    `conversations.replies` on a private channel with a token lacking `groups:history`
    returns `needed=groups:history, provided=identify,im:history,…`. Discarding them
    forced downstream to guess the scope, and a refusal that cannot be translated into an
    action blocks the caller (two orgs stuck, oto-backend #510/#532).
    `needed` can be a comma-separated LIST = "any one of these is enough"
    (observed: `channels:read,groups:read,mpim:read,im:read`).
    """

    def __init__(self, error: Optional[str], status: Optional[int] = None,
                 *, needed: Optional[str] = None, provided: Optional[str] = None):
        self.error = error or "unknown"
        self.status = status if status is not None else _SLACK_ERROR_STATUS.get(self.error, 400)
        self.needed = needed or None
        self.provided = provided or None
        super().__init__(f"Slack API error: {self.error}")


def verify_slack_signature(
    signing_secret: str,
    body: bytes,
    timestamp: str,
    signature: str,
) -> bool:
    """
    Verify Slack webhook signature.

    Args:
        signing_secret: Slack signing secret
        body: Request body bytes
        timestamp: X-Slack-Request-Timestamp header
        signature: X-Slack-Signature header

    Returns:
        True if valid
    """
    sig_basestring = f"v0:{timestamp}:{body.decode('utf-8')}"
    my_signature = "v0=" + hmac.new(
        signing_secret.encode(),
        sig_basestring.encode(),
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(my_signature, signature)


class SlackClient:
    """
    Slack API client. Multi-workspace.

    Tokens are always provided by the consumer (`bot_token` and/or
    `user_token`); `workspace` is only a label for the caller.

    Two tokens are supported per workspace:
    - **bot token** (`xoxb-`) — messages appear as the bot app. Use for
      automated/agentic actions (notifications, reactions, scheduled posts).
    - **user token** (`xoxp-`) — messages appear as the human user who installed
      the app. Use for outbound human-style com sent on behalf of that user.

    Reads route by Slack surface (see `_prefer`): channel reads (list channels,
    channel history) go through the **bot** token — the bot is invited and this
    keeps the user token's scopes minimal — while DM reads (own DMs, `open_dm`,
    `search`) go through the **user** token, since only the user sees their own
    conversations. When a single token is configured, reads fall through to it.
    Writes (`post_message`, `update_message`, `add_reaction`) accept
    `as_user=True/False`; omitted → `default_as_user` (construction, default
    False = bot-style). Every read also accepts an explicit `as_user` override.
    """

    BASE_URL = "https://slack.com/api"

    def __init__(
        self,
        bot_token: Optional[str] = None,
        user_token: Optional[str] = None,
        default_as_user: bool = False,
        workspace: Optional[str] = None,
    ):
        """
        Initialize Slack client.

        Args:
            bot_token: Bot token (`xoxb-`).
            user_token: User token (`xoxp-`). At least one of the two is required.
            default_as_user: Default mode when a method's `as_user` arg is None.
            workspace: Workspace slug, a label only (no token lookup).
        """
        self.workspace = workspace
        self.bot_token = bot_token
        self.user_token = user_token
        self.default_as_user = default_as_user
        if not self.bot_token and not self.user_token:
            raise MissingCredential("SLACK_BOT_TOKEN or SLACK_USER_TOKEN")

    def _resolve_token(self, as_user: Optional[bool]) -> str:
        mode = self.default_as_user if as_user is None else as_user
        if mode:
            if not self.user_token:
                raise ValueError("as_user=True requires SLACK_USER_TOKEN")
            return self.user_token
        if not self.bot_token:
            raise ValueError("as_user=False requires SLACK_BOT_TOKEN")
        return self.bot_token

    def _prefer(self, want_user: bool) -> bool:
        """Capability routing for reads → the `as_user` flag of the token that
        fits the operation, falling through to the other when only one token is
        configured. Channel reads fit the **bot** (invited, keeps the user
        token's scopes minimal); DM/search reads fit the **user** (only they see
        their own conversations, and `search:read` is a user-token-only scope).
        Not a legacy fallback: routes to the only usable token, and Slack still
        returns a typed error if that token genuinely can't do the operation."""
        if want_user:
            return bool(self.user_token)   # user token if present, else bot
        return not self.bot_token          # bot token if present, else user

    def _request(
        self,
        method: str,
        endpoint: str,
        as_user: Optional[bool] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Make API request. Picks token based on as_user (None = default)."""
        url = f"{self.BASE_URL}/{endpoint}"
        headers = {"Authorization": f"Bearer {self._resolve_token(as_user)}"}

        response = requests.request(method, url, headers=headers, timeout=_HTTP_TIMEOUT, **kwargs)
        response.raise_for_status()

        data = response.json()
        if not data.get("ok"):
            raise SlackError(data.get("error"), needed=data.get("needed"),
                             provided=data.get("provided"))

        return data

    def post_message(
        self,
        channel: str,
        text: str = None,
        blocks: List[Dict] = None,
        thread_ts: str = None,
        as_user: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Send a message to a channel or DM. A bare clock time or any other
        colon-bounded number in `text` is escaped so Slack cannot misread it
        as an emoji shortcode (`:51:`) — see `_escape_false_emoji_shortcodes`.

        `text` longer than Slack's recommended 4,000 characters is SPLIT into
        several `chat.postMessage` calls rather than left for Slack to
        silently truncate at 40,000: the first part keeps `thread_ts` (a new
        top-level message if none was given), every later part threads under
        the FIRST part's `ts`. The response then carries `ts_all` (every ts
        produced, in order) in addition to `ts` (always the first — the one
        to reuse for a further reply in the same thread).

        Args:
            channel: Channel ID or name
            text: Message text (fallback for blocks)
            blocks: Block Kit blocks — bypasses splitting/escaping (caller-built).
            thread_ts: Thread timestamp for reply
            as_user: True = post via user token (appears as the human user).
                False = bot token (appears as the bot app). None = client default.

        Returns:
            Message data with `ts` — plus `ts_all`/`split_into` when split.
        """
        if blocks or not text or len(text) <= _MAX_TEXT_LEN:
            data = {"channel": channel}
            if text:
                data["text"] = _escape_false_emoji_shortcodes(text)
            if blocks:
                data["blocks"] = blocks
            if thread_ts:
                data["thread_ts"] = thread_ts
            return self._request("POST", "chat.postMessage", as_user=as_user, json=data)

        parts = _chunk_text(text)
        anchor = thread_ts
        ts_all: List[str] = []
        first_resp: Dict[str, Any] = {}
        for i, part in enumerate(parts):
            reply_to = anchor or (ts_all[0] if ts_all else None)
            data = {"channel": channel, "text": _escape_false_emoji_shortcodes(part)}
            if reply_to:
                data["thread_ts"] = reply_to
            resp = self._request("POST", "chat.postMessage", as_user=as_user, json=data)
            ts_all.append(resp.get("ts"))
            if i == 0:
                first_resp = resp
        result = dict(first_resp)
        result["ts_all"] = ts_all
        result["split_into"] = len(parts)
        return result

    def delete_message(
        self,
        channel: str,
        ts: str,
        as_user: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Delete a message. Must be deleted with the same token that posted it
        (user-posted → user token, bot-posted → bot token).

        Args:
            channel: Channel ID
            ts: Message timestamp
            as_user: Same conventions as post_message.

        Returns:
            Response data
        """
        return self._request(
            "POST", "chat.delete", as_user=as_user, json={"channel": channel, "ts": ts}
        )

    def update_message(
        self,
        channel: str,
        ts: str,
        text: str = None,
        blocks: List[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Update an existing message.

        Args:
            channel: Channel ID
            ts: Message timestamp
            text: New text
            blocks: New blocks

        Returns:
            Updated message data
        """
        data = {"channel": channel, "ts": ts}
        if text:
            data["text"] = text
        if blocks:
            data["blocks"] = blocks

        return self._request("POST", "chat.update", json=data)

    def post_ephemeral(
        self,
        channel: str,
        user: str,
        text: str = None,
        blocks: List[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Send an ephemeral message (visible only to one user).

        Args:
            channel: Channel ID
            user: User ID
            text: Message text
            blocks: Block Kit blocks

        Returns:
            Message data
        """
        data = {"channel": channel, "user": user}
        if text:
            data["text"] = text
        if blocks:
            data["blocks"] = blocks

        return self._request("POST", "chat.postEphemeral", json=data)

    def get_user_info(self, user_id: str, as_user: Optional[bool] = None) -> Dict[str, Any]:
        """
        Get user information.

        Args:
            user_id: User ID
            as_user: Token override; None → bot (either token works).

        Returns:
            User data
        """
        if as_user is None:
            as_user = self._prefer(want_user=False)
        return self._request("GET", "users.info", as_user=as_user, params={"user": user_id})

    def list_channels(
        self, types: str = "public_channel", as_user: Optional[bool] = None
    ) -> List[Dict[str, Any]]:
        """
        List channels.

        Args:
            types: Channel types (public_channel, private_channel, mpim, im)
            as_user: Token override; None → route by `types` — DM-only listings
                (im/mpim) via the user token, channels via the bot token.

        Returns:
            List of channels
        """
        if as_user is None:
            wanted = {t.strip() for t in types.split(",") if t.strip()}
            as_user = self._prefer(want_user=bool(wanted) and wanted <= {"im", "mpim"})
        data = self._request("GET", "conversations.list", as_user=as_user, params={"types": types})
        return data.get("channels", [])

    def add_reaction(self, channel: str, ts: str, name: str) -> Dict[str, Any]:
        """
        Add a reaction to a message.

        Args:
            channel: Channel ID
            ts: Message timestamp
            name: Emoji name (without colons)

        Returns:
            Response data
        """
        return self._request("POST", "reactions.add", json={
            "channel": channel,
            "timestamp": ts,
            "name": name,
        })

    def history(
        self,
        channel: str,
        limit: int = 50,
        cursor: Optional[str] = None,
        oldest: Optional[str] = None,
        latest: Optional[str] = None,
        inclusive: bool = False,
        as_user: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Read recent messages from a channel (or DM/group). TOP-LEVEL ONLY —
        thread replies are obtained via `replies()`.

        Bounds probed on 2026-08-28: `oldest`/`latest` are **exclusive**
        (measured by differential: 10 messages with no bound, 4 with `oldest`), and
        `inclusive=True` includes the message sitting exactly on the bound.
        ⚠️ An invalid ts returns `invalid_ts_oldest` — so never let a
        `None` converted to a string go out.

        Args:
            channel: Channel ID
            limit: Max messages (capped at 100 by Slack)
            cursor: Pagination cursor from a previous call
            oldest: Only messages after this ts (exclusive)
            latest: Only messages before this ts (exclusive)
            inclusive: Include the messages sitting exactly on oldest/latest
            as_user: Token override; None → route by channel id — DMs (`D…`) via
                the user token, channels (`C…`/`G…`) via the bot token.

        Returns:
            Response data with "messages" array + "response_metadata.next_cursor"
        """
        if as_user is None:
            as_user = self._prefer(want_user=channel.startswith("D"))
        params: Dict[str, Any] = {"channel": channel, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        if oldest:
            params["oldest"] = oldest
        if latest:
            params["latest"] = latest
        if inclusive:
            params["inclusive"] = "true"
        return self._request("GET", "conversations.history", as_user=as_user, params=params)

    def replies(
        self,
        channel: str,
        thread_ts: str,
        limit: int = 50,
        cursor: Optional[str] = None,
        oldest: Optional[str] = None,
        latest: Optional[str] = None,
        inclusive: bool = False,
        as_user: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Read the REPLIES of a thread (`conversations.replies`).

        `conversations.history` only returns the first level: on a parent it
        reports `reply_count`/`reply_users`/`latest_reply` and **never a reply
        body**. This is the gap reported four times in eight days by three
        people (oto-backend #567/#576/#584/#592): a decision or a disagreement
        almost always lives in the thread.

        Contract PROBED live on 2026-08-28 (not deduced from the docs):
        - Slack's parameter is called **`ts`** — sending `thread_ts=` alone is
          rejected (`invalid_arguments`); the argument keeps here the name the
          message carries on the caller's side, the translation happens at the transport;
        - the **parent is always returned as `messages[0]`**, and it is **repeated on
          every page**: concatenating two pages counts it twice;
        - `limit` bounds the REPLIES (the parent comes on top: `limit=2` → 3
          messages) and pagination goes up the thread **from most recent to
          oldest**;
        - `oldest`/`latest` are **exclusive** bounds; `inclusive=True` includes
          them;
        - a `ts` with no reply returns the parent alone (`ok:true`, n=1) — this is not
          an error; an unknown `ts` returns `thread_not_found`;
        - ⚠️ Slack **SWALLOWS an unknown parameter and returns `ok:true`** (measured:
          `zzz_unknown=x` returns exactly the bare result). Only send
          parameters proven by differential — which is the case for the six above.

        Args:
            channel: Channel ID (`C…`/`G…`/`D…`) holding the thread.
            thread_ts: `ts` of the PARENT message (a reply's `thread_ts`).
            limit: Max replies per page (Slack caps at 1000, useful default ~50).
            cursor: Cursor from `response_metadata.next_cursor`.
            oldest: Only return replies AFTER this ts (exclusive).
            latest: Only return replies BEFORE this ts (exclusive).
            inclusive: Include messages sitting exactly on `oldest`/`latest`.
            as_user: Token override; None → same routing as `history`
                (DMs `D…` via the user token, channels via the bot).

        Returns:
            `{messages: [parent, …replies], has_more, response_metadata.next_cursor}`
        """
        if as_user is None:
            as_user = self._prefer(want_user=channel.startswith("D"))
        params: Dict[str, Any] = {"channel": channel, "ts": thread_ts, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        if oldest:
            params["oldest"] = oldest
        if latest:
            params["latest"] = latest
        if inclusive:
            # Slack reads a query-string boolean as TEXT ("true"/"false").
            params["inclusive"] = "true"
        return self._request("GET", "conversations.replies", as_user=as_user, params=params)

    def channel_info(self, channel: str, as_user: Optional[bool] = None) -> Dict[str, Any]:
        """A channel's metadata (`conversations.info`) — type, membership, archiving.

        Probed on 2026-08-28: answers even on a **public channel we are not a
        member of** (`is_private=False, is_member=False`). This is what makes it possible to
        know whether a channel is reachable via API BEFORE attempting anything.
        ⚠️ On a **private** channel we are not in, the answer is
        `channel_not_found` — indistinguishable from a wrong ID: downstream must say both.

        Args:
            channel: Channel ID (`C…`/`G…`/`D…`).
            as_user: Token override; None → bot (it carries `channels:read`).
        """
        if as_user is None:
            as_user = self._prefer(want_user=False)
        return self._request("GET", "conversations.info", as_user=as_user,
                             params={"channel": channel})

    def join_channel(self, channel: str, as_user: Optional[bool] = None) -> Dict[str, Any]:
        """Join a **public** channel (`conversations.join`).

        Closes the automatable half of oto-backend #549: without membership,
        `conversations.history` and `chat.postMessage` return `not_in_channel`, and
        a scheduled run can neither repair itself nor report it.

        ⚠️ **The other half is not automatable**: a **private** channel cannot be
        joined through ANY API — a human must invite the app (`/invite @…`).
        Do not call this method on a private channel: refusing it by naming the
        action is the only honest answer.

        Required scope (probed on 2026-08-28): **`channels:join`** for a bot token,
        `channels:write` for a user token. Without it, Slack returns `missing_scope` and
        names the right in `needed` — even before resolving the channel (a nonexistent
        ID also returns `missing_scope`).

        Args:
            channel: Channel ID public (`C…`).
            as_user: Token override; None → bot (joining is an act of the app).
        """
        if as_user is None:
            as_user = self._prefer(want_user=False)
        return self._request("POST", "conversations.join", as_user=as_user,
                             json={"channel": channel})

    def open_dm(self, user: str, as_user: Optional[bool] = None) -> Dict[str, Any]:
        """
        Open (or return) a direct-message channel with a user.

        Args:
            user: User ID
            as_user: Token override; None → user token (opens *your* DM with the
                person so you can read it), falling through to bot if no user token.

        Returns:
            Response data with "channel.id" usable as channel for post_message
        """
        if as_user is None:
            as_user = self._prefer(want_user=True)
        return self._request("POST", "conversations.open", as_user=as_user, json={"users": user})

    def find_user_by_email(self, email: str, as_user: Optional[bool] = None) -> Dict[str, Any]:
        """
        Look up a user by email.

        Args:
            email: Email address
            as_user: Token override; None → bot (either token works).

        Returns:
            User data
        """
        if as_user is None:
            as_user = self._prefer(want_user=False)
        return self._request("GET", "users.lookupByEmail", as_user=as_user, params={"email": email})

    def search_messages(self, query: str, count: int = 20) -> Dict[str, Any]:
        """
        Search messages across accessible channels. Requires `search:read` scope
        (only available on user tokens, not bot tokens) → always via the user token.

        Args:
            query: Slack search query (supports `in:#channel`, `from:@user`, etc.)
            count: Max results

        Returns:
            Response data with "messages.matches"
        """
        return self._request("GET", "search.messages", as_user=True, params={"query": query, "count": count})

    def file_info(self, file_id: str) -> Dict[str, Any]:
        """Get file metadata (requires files:read scope)."""
        return self._request("GET", "files.info", params={"file": file_id})

    def _file_source(self, file_id: str) -> tuple:
        """`(url_private, token, file_meta)` for a file id. The **user token**
        takes precedence (DM / private channel files = user-level access), bot fallback.
        Requires `files:read`."""
        info = self.file_info(file_id)
        meta = info.get("file", {})
        url = meta.get("url_private_download") or meta.get("url_private")
        if not url:
            raise SlackError("file_not_found")
        token = self.user_token or self.bot_token
        return url, token, meta

    def fetch_file(self, file_id: str) -> Dict[str, Any]:
        """Fetch the BYTES + metadata of a Slack file by its id (from a message's
        `files[]`) — in-memory use (agent output).

        Returns `{data: bytes, filename, mimetype}`. For a stream to disk,
        see `download_file`.
        """
        url, token, meta = self._file_source(file_id)
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
        return {
            "data": resp.content,
            "filename": meta.get("name") or meta.get("title") or file_id,
            "mimetype": meta.get("mimetype") or "application/octet-stream",
        }

    def download_file(self, file_id: str, dest: str) -> str:
        """Download a Slack file to a local path (streaming). Returns the path.

        Uses the user token (files in private channels / DMs need user-level
        access), falling back to the bot token.
        """
        url, token, _ = self._file_source(file_id)
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, stream=True, timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return dest
