"""Statistics of a professional Instagram account — READ-ONLY.

"Instagram API with Instagram Login" variant: the person logs in with their
**Instagram** account, not with Facebook, and no Facebook Page is required. The
token obtained is the account's own.

Three surfaces, separated by what they need:

- `oauth` — obtain the authorization. The only module that knows the `InstagramApp`
  (App ID + secret);
- `tokens` — keep it alive. ⚠️ This token **only renews while it
  lives**: no `refresh_token` that would survive its expiry, so a
  connection left dormant for 60 days is lost, not degraded;
- `client` + `best_hours` — use it. The data client needs ONLY the
  token and the account identifier.

⚠️ **The application's coordinates are not here, and never will be.** This
repo is public: a Meta application belongs to whoever created it, who
answers for what it requests and for whom it invites as a tester. Meta's
addresses, on the other hand, stay named — naming what you call is a client's job.

This package is **synchronous** and adds no dependency: `requests`, the lib's
foundation, is enough for this API.
"""
from __future__ import annotations

from .best_hours import compute_best_hours
from .client import (
    ACCOUNT_INSIGHT_METRICS,
    ACCOUNT_INSIGHTS_MAX_DAYS,
    MEDIA_INSIGHT_METRICS,
    InstagramClient,
    flatten_insights,
)
from .config import (
    LONG_LIVED_TTL_DAYS,
    MIN_TOKEN_AGE_HOURS,
    RENEW_WHEN_REMAINING_DAYS,
    SCOPES,
    InstagramApp,
)
from .errors import (
    InstagramApiError,
    InstagramAuthExpired,
    InstagramAuthRefused,
    InstagramError,
)
from .oauth import InstagramGrant, authorize_url, connect, parse_token_exchange
from .tokens import (
    expiry,
    is_expired,
    iso,
    needs_refresh,
    parse_ts,
    refresh_long_lived,
    utcnow,
)

__all__ = [
    "ACCOUNT_INSIGHTS_MAX_DAYS",
    "ACCOUNT_INSIGHT_METRICS",
    "InstagramApiError",
    "InstagramApp",
    "InstagramAuthExpired",
    "InstagramAuthRefused",
    "InstagramClient",
    "InstagramError",
    "InstagramGrant",
    "LONG_LIVED_TTL_DAYS",
    "MEDIA_INSIGHT_METRICS",
    "MIN_TOKEN_AGE_HOURS",
    "RENEW_WHEN_REMAINING_DAYS",
    "SCOPES",
    "authorize_url",
    "compute_best_hours",
    "connect",
    "expiry",
    "flatten_insights",
    "is_expired",
    "iso",
    "needs_refresh",
    "parse_token_exchange",
    "parse_ts",
    "refresh_long_lived",
    "utcnow",
]
