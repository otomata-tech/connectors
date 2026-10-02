"""Où l'on tape chez Meta, et sous quelle identité d'application.

Même partage qu'`instagram_meta` : les ADRESSES publiques de la Marketing API
restent ici ; l'IDENTITÉ de l'application (App ID, secret, configuration Facebook
Login for Business) est fournie par l'appelant, sans défaut — ce dépôt est public,
et une application Meta appartient à qui l'a créée.

Le client de DONNÉES n'a besoin que du jeton : `MetaAdsApp` ne sert qu'à
l'échange du code (`oauth.py`).
"""
from __future__ import annotations

from dataclasses import dataclass, fields

#: Version de la Graph API — celle des exemples de Facebook Login for Business.
GRAPH_API_VERSION = "v25.0"

GRAPH_API_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

#: Le dialogue Facebook Login for Business. ⚠️ Il prend un `config_id`, PAS de
#: `scope` : les permissions (`ads_read`, `business_management`) et le TYPE de jeton
#: émis sont fixés dans la configuration créée côté application Meta.
DIALOG_URL = f"https://www.facebook.com/{GRAPH_API_VERSION}/dialog/oauth"

#: Échange du code contre un jeton.
TOKEN_URL = f"{GRAPH_API_BASE}/oauth/access_token"

#: Borne de chaque appel HTTP sortant.
HTTP_TIMEOUT = 30.0

#: Niveaux de l'arbre publicitaire, et l'arête qui les liste sous un compte.
LEVELS: dict[str, str] = {
    "campaign": "campaigns",
    "adset": "adsets",
    "ad": "ads",
}

#: Champs rendus par défaut, par niveau — une vue courte ; `fields` les remplace.
DEFAULT_FIELDS: dict[str, str] = {
    "campaign": "id,name,status,effective_status,objective,daily_budget,"
                "lifetime_budget,start_time,stop_time,created_time",
    "adset": "id,name,status,effective_status,campaign_id,daily_budget,"
             "lifetime_budget,optimization_goal,billing_event,start_time,end_time",
    "ad": "id,name,status,effective_status,campaign_id,adset_id,creative{id,name},"
          "created_time",
}

AD_ACCOUNT_FIELDS = ("id,account_id,name,account_status,currency,timezone_name,"
                     "amount_spent,business{id,name}")

#: Métriques d'insights demandées quand l'appelant n'en choisit pas.
DEFAULT_INSIGHT_FIELDS = ("spend,impressions,reach,frequency,clicks,cpc,cpm,ctr,"
                          "actions,cost_per_action_type")

#: Fenêtre d'attribution par défaut du GET /insights chez Meta.
DEFAULT_ATTRIBUTION_WINDOWS = ("7d_click", "1d_view")

#: Niveaux d'agrégation acceptés par `/insights`.
INSIGHT_LEVELS = ("account", "campaign", "adset", "ad")


@dataclass(frozen=True)
class MetaAdsApp:
    """L'application Meta au nom de laquelle on demande le consentement.

    `config_id` = la configuration Facebook Login for Business (permissions + type
    de jeton). `app_secret` est un VRAI secret : il n'apparaît dans aucun message.
    """

    app_id: str
    app_secret: str
    config_id: str

    def __post_init__(self) -> None:
        # Un champ vide lève ICI : plus tard, Meta rendrait un refus qu'on lirait
        # comme « l'utilisatrice n'a pas autorisé ».
        vides = [f.name for f in fields(self) if not str(getattr(self, f.name)).strip()]
        if vides:
            raise ValueError(
                f"MetaAdsApp: missing {', '.join(vides)}. These are set by whoever "
                f"deploys the connector — there is no default.")
