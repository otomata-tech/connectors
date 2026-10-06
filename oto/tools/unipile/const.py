"""Constants and shape helpers of the Unipile connector.

Extracted from `client.py` (split by domain) — content unchanged. These names
remain re-exported by `oto.tools.unipile.client`: it is the historical import
path, and it is frozen.
"""

from __future__ import annotations

import re
from typing import Optional

DEFAULT_DSN = "api.unipile.com"
# (connect, read) in seconds — bounds the blocking: a silent upstream socket made
# the call hang until the MCP client's 300s cutoff (unipile_me, #114).
_REQUEST_TIMEOUT = (10, 120)
# #238: the Recruiter search BY URL (talent/search) can HANG indefinitely
# when the URL's `searchContextId` is expired/dead on the LinkedIn side — the endpoint
# returns neither an error nor an empty result → opaque MCP timeout at 180s. Dedicated short
# read timeout: we fail BEFORE the MCP ceiling with a CLEAN and actionable error.
_URL_SEARCH_TIMEOUT = (10, 75)
# Scrape (structured search + company page): LinkedIn/Unipile can make the call
# HANG ~120s (overload / rate-limit that queues) — seen 2026-07-21, 166
# ReadTimeouts of 121s that froze the agent 2 min each. Dedicated short read timeout:
# fail fast (60s) with an actionable error rather than freeze.
_SCRAPE_TIMEOUT = (10, 60)

# LinkedIn home feed: LinkedIn exposes NO feed endpoint on the Unipile API
# side. The only path is the Voyager Magic Route, exposed in v2 as the generic proxy
# `POST /v2/{account_id}/linkedin/` (proxyRequest): we relay a raw Voyager
# request. ⚠️ Voyager is NOT contractual: this GraphQL queryId and
# the JSON schema can break when LinkedIn evolves its internal API
# (devtools capture on linkedin.com/feed to refresh it). Source of the queryId:
# https://developer.unipile.com/docs/get-raw-data-example
FEED_QUERY_ID = "voyagerFeedDashMainFeed.7a50ef8ba5a7865c23ad5df46f735709"

# Providers whose messaging is organized by INBOX. Unipile documents TWO endpoint
# shapes for the same operation — "Use `GET /v2/:account_id/chats` or
# `GET /v2/:account_id/inboxes/:inbox_id/chats` **if the provider uses inboxes**"
# (messaging v2 migration guide) — and answers **501** to the wrong one, IN BOTH
# DIRECTIONS. The client only served LinkedIn when the inbox shape arrived (live delta
# 2026-07-06): the switch was hardcoded, so also applied to WhatsApp/Telegram/
# Instagram/Messenger/Twitter, which have no inbox → 501 on `op="list"` for these five
# channels, even though their account is properly connected. The shape is therefore DECLARED per
# provider (below) — not guessed per calling channel, not frozen to a single model.
_INBOX_PROVIDERS = {"LINKEDIN"}
# Assumed provider when the caller declares none: the client is historically
# LinkedIn-first (`UNIPILE_LINKEDIN_ACCOUNT_ID`, discovery of the first linkedin account), and
# a caller that says nothing expects the previous behavior.
_DEFAULT_PROVIDER = "LINKEDIN"

# Path prefix per LinkedIn product (search & co.).
_API_PREFIX = {
    "classic": "/linkedin/search",
    "sales_navigator": "/linkedin/sales-navigator/search",
    "recruiter": "/linkedin/recruiter/search",
}


def cursor_with_limit(cursor: str, limit: int) -> str:
    """Rewrite `limit` INSIDE a Unipile cursor (base64 of `{limit, startIndex}`).

    The Unipile API freezes the `limit` of the 1st call in the cursor and then IGNORES
    the `limit` param (feedback #179: a pagination started at limit=3 stayed
    stuck at 3/page — hundreds of calls for a whole network). The current call's
    limit must win. Unexpected cursor shape (non-base64,
    non-JSON, no limit key) → returned as is, the API decides."""
    import base64
    import json
    try:
        data = json.loads(base64.b64decode(cursor + "=" * (-len(cursor) % 4)))
        if isinstance(data, dict) and "limit" in data:
            data["limit"] = int(limit)
            return base64.b64encode(json.dumps(data).encode()).decode()
    except Exception:  # noqa: BLE001 — opaque cursor: never blocking
        pass
    return cursor


def _sections_param(sections: str) -> Optional[list[str]]:
    """Map the `sections` value to the v2 `with_sections` param.

    Input: `"*"` (everything) or a comma-separated list of bare names
    (`experience`, `education`…). v2: `with_sections=linkedin_<name>` (and
    `linkedin_*` = everything). `"*"`/empty → None (server default = everything)."""
    s = (sections or "").strip()
    if not s or s in ("*", "linkedin_*"):
        return None
    out: list[str] = []
    for part in s.split(","):
        p = part.strip()
        if not p:
            continue
        out.append(p if p.startswith("linkedin_") else f"linkedin_{p}")
    return out or None


def _slug_from_company_url(url: str) -> Optional[str]:
    """Extract the slug from a LinkedIn company URL (`…/company/<slug>[/…]`)."""
    if not url:
        return None
    m = re.search(r"/company/([^/?#]+)", url)
    return m.group(1) if m else None
