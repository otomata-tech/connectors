"""This connector's refusals — distinct because they call for distinct actions.

- **dead** authorization (revoked, password changed, access removed) → redo the
  consent;
- consent **refused** by Meta (unpublished app, non-tester account, undeclared
  redirect) → the operator must sort it out;
- **throttled** call (Marketing API quota) → wait, then retry;
- everything else → the call failed, retrying makes sense.

No message from here names a tool or a screen: the lib does not know its surface.
"""
from __future__ import annotations

from typing import Optional


class MetaAdsError(RuntimeError):
    """Root — everything this connector raises itself."""


class MetaAdsAuthExpired(MetaAdsError):
    """The token is worthless (Graph code 190/102, or 401)."""


class MetaAdsAuthRefused(MetaAdsError):
    """Meta refused the consent or the code exchange. `reason` = returned type."""

    def __init__(self, message: str, reason: str = ""):
        super().__init__(message)
        self.reason = reason


class MetaAdsApiError(MetaAdsError):
    """The call failed. `status` = HTTP, `code`/`subcode` = Graph's.

    The raw body never enters here: the request echo can carry an
    identifier, and this text ends up in an agent transcript."""

    def __init__(self, message: str, status: Optional[int] = None,
                 code: Optional[int] = None, subcode: Optional[int] = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.subcode = subcode


class MetaAdsThrottled(MetaAdsApiError):
    """Marketing API quota reached (codes 4, 17, 613, 80000-80014…) — wait."""
