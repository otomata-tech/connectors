"""Renewing the authorization — when, and how.

**The property that governs this whole module: this token only renews while
it lives.** There is no `refresh_token` at Meta on this product; we
exchange the current token for a fresh 60-day token, and if the current one is
dead, there is nothing to exchange. A connection that is not renewed in time
is not degraded: it is lost, and only the user can redo it.

Two consequences, carried by the code rather than by a comment:

- **`is_expired` is asked BEFORE `needs_refresh`**. On a dead token,
  `needs_refresh` returns `False` — not because it is fine, but because
  renewal can no longer do anything: a new consent is needed, and
  calling Meta to be told so only adds a round trip to
  every call of a broken connection;
- **the threshold is wide** (`RENEW_WHEN_REMAINING_DAYS`, 53 days out of 60). A tight
  threshold would save calls and make the connection's survival depend on the
  luck of a use in the final stretch. The calculation works the other
  way around: what we want is for a use — even very sporadic — to be enough to hold.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from . import _transport
from .config import (
    GRAPH_ROOT,
    HTTP_TIMEOUT,
    LONG_LIVED_TTL_DAYS,
    MIN_TOKEN_AGE_HOURS,
    RENEW_WHEN_REMAINING_DAYS,
)
from .errors import InstagramAuthExpired


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    """UTC timestamp, whole seconds, `Z` suffix — the form we store."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value: Optional[str]) -> Optional[datetime]:
    """An ISO 8601 timestamp as an AWARE datetime, or `None` if absent/unreadable.

    A naive value is read as UTC: that is what this package writes, and assuming
    local time would shift the expiry by several hours depending on the machine
    reading it back — enough to renew too late on a changeover day."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def expiry(expires_at: Optional[str] = None,
           issued_at: Optional[str] = None) -> Optional[datetime]:
    """The known expiry: `expires_at`, else `issued_at` + 60 days, else `None`.

    `None` means "we don't know", not "it's fine": without a reference, we don't
    renew blindly — Meta's refusal will settle it. This case should
    not exist for a caller that stores what `connect` returned to it."""
    exp = parse_ts(expires_at)
    if exp:
        return exp
    emis = parse_ts(issued_at)
    return emis + timedelta(days=LONG_LIVED_TTL_DAYS) if emis else None


def is_expired(expires_at: Optional[str] = None, issued_at: Optional[str] = None,
               now: Optional[datetime] = None) -> bool:
    """Is the authorization already dead? Unknown expiry ⟹ `False`."""
    exp = expiry(expires_at, issued_at)
    return bool(exp and exp <= (now or utcnow()))


def needs_refresh(expires_at: Optional[str] = None, issued_at: Optional[str] = None,
                  now: Optional[datetime] = None) -> bool:
    """Must we renew NOW? See the contract at the top of the module.

    Three `False` values that don't mean the same thing, and which are better read
    here than deduced: unknown expiry (nothing to compute), token too YOUNG (Meta
    refuses to renew before 24 h — renewing immediately would cause a refusal
    loop), and token already dead (`is_expired` is the question to ask)."""
    maintenant = now or utcnow()
    exp = expiry(expires_at, issued_at)
    if exp is None or exp <= maintenant:
        return False
    emis = parse_ts(issued_at)
    if emis and maintenant - emis < timedelta(hours=MIN_TOKEN_AGE_HOURS):
        return False
    return exp - maintenant < timedelta(days=RENEW_WHEN_REMAINING_DAYS)


def refresh_long_lived(access_token: str, *,
                       expires_at: Optional[str] = None,
                       session: Optional[requests.Session] = None) -> dict:
    """Exchanges the current token for a fresh 60-day token.

    Returns `{"access_token": …, "expires_in": …}` — the caller derives the new
    expiry from it and stores it wherever it stores the token. Raises `InstagramAuthExpired` if
    Meta refuses: at that point, the only possible gesture is a new consent,
    and `expires_at` (when the caller knows it) travels with the error so that it
    can state the DATE rather than a "it no longer works".

    ⚠️ This renewal does NOT take the application secret — the token
    renews itself. That is also what makes this path workable from a
    periodic job that doesn't need the application's coordinates."""
    if not access_token:
        raise InstagramAuthExpired(
            "No Instagram authorization to renew: the account is not connected.",
            expires_at)
    http = session or requests
    # Same remark as in `oauth.connect`: Meta only serves this endpoint via GET,
    # parameters in the URL. What the rule protects is upheld otherwise — no
    # `raise_for_status()`, and `_transport` returns neither the URL nor the raw body.
    r = http.get(f"{GRAPH_ROOT}/refresh_access_token", params={
        "grant_type": "ig_refresh_token",
        "access_token": access_token,
    }, timeout=HTTP_TIMEOUT)
    charge = _transport.lire(r, "renewing the authorization",
                             expires_at=expires_at)
    jeton = charge.get("access_token")
    if not jeton:
        raise InstagramAuthExpired(
            "Instagram answered the renewal without returning a new authorization.",
            expires_at)
    return {"access_token": str(jeton),
            "expires_in": int(charge.get("expires_in") or LONG_LIVED_TTL_DAYS * 86_400)}
