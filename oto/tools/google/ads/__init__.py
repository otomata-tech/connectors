"""Google Ads — read access with an OAuth2 user access token (REST, scope `adwords`).

`GoogleAdsClient` is the entry point. Read-only: no method mutates anything.
"""
from __future__ import annotations

from .client import (
    API_VERSION,
    SCOPES,
    GoogleAdsClient,
    GoogleAdsError,
    check_select,
    customer_id,
    flatten_rows,
)

__all__ = [
    "API_VERSION", "SCOPES", "GoogleAdsClient", "GoogleAdsError", "check_select",
    "customer_id", "flatten_rows",
]
