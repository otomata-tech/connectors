"""Error types of the Unipile connector (and the 429 delay parsing).

Extracted from `client.py` — content unchanged, re-exported by `client.py`.
"""

from __future__ import annotations

import re
from typing import Optional


class UnipileError(RuntimeError):
    """Unipile API error, message passed through as is.

    `status_code` = upstream HTTP code when the error comes from a Unipile response
    (same contract as `oto.tools.common.UpstreamHTTPError`: lets
    consumers route a 4xx as a handled error, not a bug), None otherwise
    (network error, config, identity mismatch).
    """

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class UnipileRateLimited(UnipileError):
    """Unipile 429: upstream quota reached. LinkedIn caps company/profile pages
    at ~100/12h PER ACCOUNT ("We only allow 100 requests. Retry in N hours"). Dedicated
    type + parsed delay → the caller STOPS instead of hammering (251 calls lost
    in 12h, seen 2026-07-21). `retry_after` = seconds before retry, None if unreadable."""

    def __init__(self, message: str, retry_after: Optional[int] = None):
        super().__init__(message, status_code=429)
        self.retry_after = retry_after


_RETRY_RE = re.compile(r"retry in\s+(\d+)\s*(hour|hr|minute|min|second|sec)", re.I)


def _parse_retry_after(msg: str) -> Optional[int]:
    """Seconds before retry from a 429 body ("Retry in 12 hours"). None otherwise."""
    m = _RETRY_RE.search(msg or "")
    if not m:
        return None
    return int(m.group(1)) * {"h": 3600, "m": 60, "s": 1}[m.group(2).lower()[0]]


def _retry_after_header(headers) -> Optional[int]:
    """Seconds before retry from the HTTP `Retry-After` header (seconds form).
    None if absent, or in HTTP-date form (the body then takes over)."""
    raw = (headers or {}).get("Retry-After") if hasattr(headers, "get") else None
    if raw is None:
        return None
    raw = str(raw).strip()
    return int(raw) if raw.isdigit() else None
