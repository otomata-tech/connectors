"""Meta Ads (Marketing API) — LECTURE SEULE : comptes publicitaires, campagnes,
ad sets, pubs, insights.

Trois surfaces :

- `oauth` — obtenir l'autorisation (Facebook Login for Business). Seul module qui
  connaît la `MetaAdsApp` (App ID, secret, configuration) ;
- `client` — s'en servir. N'a besoin QUE du jeton ;
- `errors` — les refus, séparés par le geste qu'ils appellent.

⚠️ Les coordonnées de l'application ne sont pas ici, et n'y seront pas : ce dépôt
est public, et une application Meta appartient à celui qui l'a créée.

Synchrone, sans dépendance au-delà de `requests`.
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
