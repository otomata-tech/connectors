"""Google Search Console — via a service account key (Search Console API).

`SearchConsoleClient` is the entry point. The token comes from
`google_analytics.auth`, which already holds the service account flow.
"""
from __future__ import annotations

try:
    from .client import (
        DEFAULT_ROW_LIMIT,
        SearchConsoleClient,
        SearchConsoleDenied,
        SearchConsoleError,
        SearchConsoleInvalid,
        SearchConsoleServiceDisabled,
        build_filters,
        flatten_inspection,
        flatten_performance,
        site_url,
    )
    from ..google_analytics.auth import ServiceAccountAuthError
except ImportError as e:
    # Same rule as `google_analytics`: say what to install, at the source.
    if not (getattr(e, "name", None) or "").startswith("google"):
        raise
    raise ImportError(
        "the `search_console` connector needs the `google` extra — install "
        f"`oto-core[google]` (`{e.name}` is missing): the service account key "
        "signs its assertion with `google-auth`.") from e

__all__ = [
    "DEFAULT_ROW_LIMIT", "SearchConsoleClient", "SearchConsoleDenied",
    "SearchConsoleError", "SearchConsoleInvalid", "SearchConsoleServiceDisabled",
    "ServiceAccountAuthError", "build_filters", "flatten_inspection",
    "flatten_performance", "site_url",
]
