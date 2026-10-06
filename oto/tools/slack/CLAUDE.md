# Slack connector (`oto.tools.slack`)

Multi-workspace Slack Web API client. Source: `client.py` (`SlackClient`), text preprocessed in
`text.py` (sibling module, pure, no I/O). Exposed over MCP (`slack_*`) by the consumer.

Error handling: Slack's logical rejections (`{"ok": false, "error": "<code>"}` with HTTP 200) are
translated into a typed error carrying `.status` (upstream 4xx = input rejected, 5xx = Slack incident) — see
`_SLACK_ERROR_STATUS`. On a `missing_scope`, `SlackError` also carries **`needed`/`provided`**:
Slack ITSELF NAMES the missing right, downstream relays it instead of guessing.

## 1. Multi-workspace model & tokens

A `SlackClient` targets **one workspace**. Its tokens are **always supplied by the consumer**:
the lib reads no secret. `workspace="<slug>"` is only a label.

| | parameter | usage |
|---|---|---|
| bot token (`xoxb-`) | `bot_token` | read + post "as the app" |
| user token (`xoxp-`) | `user_token` | post "as the user" (`as_user=True`) |

At least one of the two is required; none ⇒ `MissingCredential`.

`post_message`/`update_message`/`open_dm`/`add_reaction` accept `as_user=True|False`; if omitted →
the client's `default_as_user` (default `False` = bot). **Reading** (channels, history, replies,
`channel_info`, find-user) and **`join_channel`** → the bot is enough.

**A 2nd workspace** = another `SlackClient` built with that workspace's tokens.

## 2. Slack API gotchas

⚠️ **`history()` only returns the FIRST LEVEL.** On a parent message it reports
`reply_count`/`reply_users`/`latest_reply` but **never a reply body**: a thread's replies
are obtained via **`replies()`** (`conversations.replies`). The probed contract — the parameter is called
`ts`, the parent comes back as `messages[0]` and repeats on every page, `limit` bounds the replies without
counting the parent, `oldest`/`latest` are exclusive — is written in the method's docstring and
verified by `tests/test_slack_thread_replies.py`. ⚠️ Called with the `ts` of a **reply**, Slack returns
that single message with `ok:true`: an "empty thread" that is not one, to be detected downstream
(`messages[0].thread_ts != .ts`).

⚠️ **`join_channel()` only applies to PUBLIC channels.** A private channel cannot be joined through **any**
Slack API — a human who is already a member must invite the app. `channel_info()` exists to decide
public/private **before** attempting anything; it answers on a public channel not yet joined, but returns
`channel_not_found` on a private channel where the app is not — indistinguishable from a wrong ID, so downstream must
say both.

⚠️ **`find_user_by_email` depends on the Slack account's REAL email.** `users.lookupByEmail` wants the address
the person signed up to Slack with, not necessarily their work email: a perfectly valid work address
returns `users_not_found`. Lookup fails ⇒ check the target's sign-up email.

⚠️ **No `whoami` method.** To know which workspace and under which identity we act, you have to call the
`auth.test` API directly.

## 3. `post_message` — two guards on the text

The text goes through `text.py` BEFORE leaving:

- **Escaping false emoji**: Slack reads any purely numeric `:token:` as a shortcode, even
  unknown — a time `"20:51:"` followed by a punctuation `:` reads as `:51:` and swallows the digits.
  `escape_false_emoji_shortcodes` breaks these tokens with a zero-width space (invisible), except
  the two real exceptions in the default set (`:100:`, `:1234:`).
- **Split beyond 4,000 characters** — **recommended** limit by Slack: beyond it, Slack refuses
  nothing, it **TRUNCATES silently** at 40,000, and a single send comes out as several messages.
  `post_message` splits itself (`chunk_text`, cutting on a word or a newline), posts each
  part and returns **`ts_all`** (all the `ts`, in order) in addition to `ts` — always the FIRST, the anchor
  to reuse to reply in the same thread. Without a supplied `thread_ts`, the following parts thread
  under the first; with a supplied `thread_ts`, all stay attached to it.

## 4. Onboarding a new user

1. Create an app at https://api.slack.com/apps (or install a shared app) on the target workspace.
2. **Scopes** (OAuth & Permissions): bot read / post = `chat:write`, `channels:read`,
   `users:read.email`, `im:write`; **joining a public channel** = `channels:join`; search =
   `search:read` (⚠️ **user token** scope only); post "as user" = an additional `xoxp-` user token.
   ⚠️ **Reading history AND threads requires a `<surface>:history` scope PER surface** —
   `channels:history` (public), `groups:history` (private), `im:history` (DM), `mpim:history` (group
   DM); `conversations.replies` requires the same as `conversations.history`.
3. **Install** the app → retrieve the `Bot User OAuth Token` (`xoxb-`) and, if needed, the `User OAuth
   Token` (`xoxp-`), then hand them to the consumer, which passes them to the `SlackClient`.
