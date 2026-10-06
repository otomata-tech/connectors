"""Where we call Meta, and under which application identity.

Same split as `instagram_meta`: the public ADDRESSES of the Marketing API
stay here; the application IDENTITY (App ID, secret, Facebook
Login for Business configuration) is supplied by the caller, with no default — this repo is public,
and a Meta application belongs to whoever created it.

The DATA client only needs the token: `MetaAdsApp` is only used for
the code exchange (`oauth.py`).
"""
from __future__ import annotations

from dataclasses import dataclass, fields

#: Graph API version — the one in the Facebook Login for Business examples.
GRAPH_API_VERSION = "v25.0"

GRAPH_API_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

#: The Facebook Login for Business dialog. ⚠️ It takes a `config_id`, NOT a
#: `scope`: the permissions (`ads_read`, `business_management`) and the TYPE of token
#: issued are fixed in the configuration created on the Meta application side.
DIALOG_URL = f"https://www.facebook.com/{GRAPH_API_VERSION}/dialog/oauth"

#: Exchange the code for a token.
TOKEN_URL = f"{GRAPH_API_BASE}/oauth/access_token"

#: Limit on each outgoing HTTP call.
HTTP_TIMEOUT = 30.0

#: Levels of the ad tree, and the edge that lists them under an account.
LEVELS: dict[str, str] = {
    "campaign": "campaigns",
    "adset": "adsets",
    "ad": "ads",
}

#: Fields returned by default, per level — a short view; `fields` replaces them.
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

#: Insight metrics requested when the caller picks none.
DEFAULT_INSIGHT_FIELDS = ("spend,impressions,reach,frequency,clicks,cpc,cpm,ctr,"
                          "actions,cost_per_action_type")

#: Default attribution window of Meta's GET /insights.
DEFAULT_ATTRIBUTION_WINDOWS = ("7d_click", "1d_view")

#: Aggregation levels accepted by `/insights`.
INSIGHT_LEVELS = ("account", "campaign", "adset", "ad")


@dataclass(frozen=True)
class MetaAdsApp:
    """The Meta application on whose behalf consent is requested.

    `config_id` = the Facebook Login for Business configuration (permissions + token
    type). `app_secret` is a REAL secret: it appears in no message.
    """

    app_id: str
    app_secret: str
    config_id: str

    def __post_init__(self) -> None:
        # An empty field raises HERE: later, Meta would return a refusal that we'd read
        # as "the user did not authorize".
        vides = [f.name for f in fields(self) if not str(getattr(self, f.name)).strip()]
        if vides:
            raise ValueError(
                f"MetaAdsApp: missing {', '.join(vides)}. These are set by whoever "
                f"deploys the connector — there is no default.")
