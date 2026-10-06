"""Meta Ads (Marketing API) — READ-ONLY: ad accounts, campaigns,
ad sets, ads, insights.

Three surfaces:

- `oauth` — obtain the authorization (Facebook Login for Business). Only module that
  knows the `MetaAdsApp` (App ID, secret, configuration);
- `client` — use it. Needs ONLY the token;
- `errors` — the refusals, separated by the action they call for.

⚠️ The application's credentials are not here, and never will be: this repo
is public, and a Meta application belongs to whoever created it.

Synchronous, no dependency beyond `requests`.
"""
from __future__ import annotations

from .client import MetaAdsClient, ad_account_id, objet_id, rapport_id
from .config import (
    DEFAULT_FIELDS,
    DEFAULT_INSIGHT_FIELDS,
    GRAPH_API_VERSION,
    INSIGHT_LEVELS,
    LEVELS,
    MetaAdsApp,
)
from .errors import (
    MetaAdsApiError,
    MetaAdsAuthExpired,
    MetaAdsAuthRefused,
    MetaAdsError,
    MetaAdsThrottled,
)
from .oauth import MetaAdsGrant, authorize_url, connect

__all__ = [
    "objet_id",
    "rapport_id",
    "DEFAULT_FIELDS",
    "DEFAULT_INSIGHT_FIELDS",
    "GRAPH_API_VERSION",
    "INSIGHT_LEVELS",
    "LEVELS",
    "MetaAdsApiError",
    "MetaAdsApp",
    "MetaAdsAuthExpired",
    "MetaAdsAuthRefused",
    "MetaAdsClient",
    "MetaAdsError",
    "MetaAdsGrant",
    "MetaAdsThrottled",
    "ad_account_id",
    "authorize_url",
    "connect",
]
