"""The three refusals of this connector — distinct because they call for three gestures.

The point isn't the taxonomy: it is that a caller knows **what to tell whom**.
On this API, three causes look alike in HTTP (all 400s with an
`OAuthException` body) and look nothing alike from the person's point of view:

- their authorization is **dead** (60 days elapsed, or revoked) → they must
  reconnect their account, and nothing else can do it for them;
- Meta **refuses the consent** — typically because the account isn't
  invited as a tester while the application is unpublished → they can't
  fix it, the operator can;
- the call **failed** for anything else → retrying makes sense.

Confusing them is costly in this precise way: an authorization refusal presented
as an outage makes people retry indefinitely, and an outage presented as a dead
authorization makes them redo a perfectly useless consent.

⚠️ **No message from here names a tool or a screen.** The lib doesn't know the
surface calling it (an MCP server, a CLI, a periodic job); it is up to
that surface to translate a fact into a gesture, with its product's words.
"""
from __future__ import annotations

from typing import Optional


class InstagramError(RuntimeError):
    """Root — everything this connector raises itself."""


class InstagramAuthExpired(InstagramError):
    """The authorization is dead: token expired, revoked, or renewal refused.

    A long-lived Instagram token lives 60 days and **only renews while it
    lives**: there is no `refresh_token` that would survive its expiry. Past
    that term, nothing recovers it — a new consent is needed.

    `expires_at` carries the expiry when known, so that the caller can
    STATE it. "Your authorization has expired" without a date reads as an outage;
    with the date, it reads as what it is."""

    def __init__(self, message: str, expires_at: Optional[str] = None):
        super().__init__(message)
        self.expires_at = expires_at


class InstagramAuthRefused(InstagramError):
    """Meta refused the consent or the code exchange.

    `reason` carries the code Meta returned when there is one, so that
    the caller composes its message without re-guessing the cause."""

    def __init__(self, message: str, reason: str = ""):
        super().__init__(message)
        self.reason = reason


class InstagramApiError(InstagramError):
    """The call failed for another reason — retrying makes sense.

    `status` = the HTTP code when there is one. **The response body doesn't go
    in**: it carries the token on some error paths, and that text ends up
    in an agent transcript."""

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status
