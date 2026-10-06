"""The DATA client — profile, media, insights of a professional account.

It only knows two things: a token and the account identifier. No App ID,
no secret: they are only used to obtain the token (`oauth.py`), not to use it.

**Synchronous**, like the rest of the lib: this API is plain HTTPS, and nothing
upstream requires an event loop. A caller that lives in a loop
(a single-loop server, for example) runs these calls in a worker thread;
that is its business, and it costs less than one more async package.

**Raw output is returned as-is.** These methods don't recompose, rename
or invent fields: what Meta returns is what the caller receives. The
only shaping is the flattening of insights, whose nested structure
(`data[].total_value.value` OR `data[].values[0].value`) is not
information but an artifact of the format.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Optional

import requests

from . import _transport
from .config import GRAPH_API_BASE, HTTP_TIMEOUT

#: Insight metrics per `media_product_type`. **No fallback list**: if
#: a type is not here, we SAY so rather than request a generic set — a refused
#: metric fails the whole call, and the message would then talk about the
#: metric instead of the media type.
MEDIA_INSIGHT_METRICS: dict[str, tuple[str, ...]] = {
    "FEED": ("reach", "views", "likes", "comments", "saved", "shares",
             "total_interactions", "follows", "profile_visits"),
    "AD": ("reach", "views", "likes", "comments", "saved", "shares",
           "total_interactions", "follows", "profile_visits"),
    "REELS": ("reach", "views", "likes", "comments", "saved", "shares",
              "total_interactions", "ig_reels_avg_watch_time",
              "ig_reels_video_view_total_time"),
    "STORY": ("reach", "views", "replies", "shares", "total_interactions",
              "follows", "profile_visits"),
}

#: ACCOUNT metrics, all with `metric_type=total_value`, `period=day`.
#: ⚠️ `profile_views`, `website_clicks` and `impressions` NO LONGER exist in this
#: API variant: requesting any of the three fails the entire call, not
#: just the metric. `profile_links_taps` replaces the first two.
ACCOUNT_INSIGHT_METRICS: tuple[str, ...] = (
    "reach", "views", "accounts_engaged", "total_interactions",
    "likes", "comments", "saves", "shares", "profile_links_taps",
)

#: Maximum window accepted on `since`/`until` for account insights.
ACCOUNT_INSIGHTS_MAX_DAYS = 30

PROFILE_FIELDS = ("user_id,username,name,biography,followers_count,follows_count,"
                  "media_count,profile_picture_url,website")

MEDIA_FIELDS = ("id,caption,timestamp,media_type,media_product_type,"
                "like_count,comments_count,permalink")


def flatten_insights(payload: dict) -> dict[str, Any]:
    """`{metric: value}` from an insights response.

    Two shapes depending on the requested `metric_type`: `total_value.value` on one
    side, a dated `values` list on the other, of which we take the first entry. A
    metric with no value is 0 — that is what Meta means by an empty list,
    and returning `None` would force every reader to translate it back."""
    out: dict[str, Any] = {}
    for ins in payload.get("data", []) or []:
        nom = ins.get("name")
        if not nom:
            continue
        total = ins.get("total_value") or {}
        if "value" in total:
            out[nom] = total["value"]
            continue
        valeurs = ins.get("values") or []
        out[nom] = valeurs[0].get("value") if valeurs else 0
    return out


class InstagramClient:
    """Read-only access to a professional Instagram account.

    `renew` — called ONCE per client if Meta rejects the token mid-call
    (revocation, rotation), and must return a new token. It is a catch-up, not
    the renewal policy: that one is preventive and lives with the caller
    (`tokens.needs_refresh`), which alone knows where the token is stored. Without `renew`, a
    rejection bubbles up as-is — which is the right default for a caller that has nothing
    to rewrite.
    """

    def __init__(self, access_token: str, user_id: str, *,
                 renew: Optional[Callable[[], str]] = None,
                 session: Optional[requests.Session] = None):
        if not access_token or not user_id:
            raise ValueError(
                "InstagramClient: token and account identifier required — both "
                "come from the consent (`oauth.connect`).")
        self.access_token = access_token
        self.user_id = str(user_id)
        self._renew = renew
        self._renouvele = False
        self._http = session or requests

    def _get(self, chemin: str, geste: str, **params: Any) -> dict:
        """A data GET, with a single token catch-up.

        "Only once" is the point: without a counter, a token that Meta refuses
        for a reason other than its death would make the call loop between the refusal and
        the renewal."""
        url = f"{GRAPH_API_BASE}{chemin}"
        r = self._http.get(url, params={**params, "access_token": self.access_token},
                           timeout=HTTP_TIMEOUT)
        if (r.status_code != 200 and self._renew and not self._renouvele
                and _transport.jeton_mort(r.status_code, _corps(r))):
            self._renouvele = True
            self.access_token = self._renew()
            r = self._http.get(url, params={**params, "access_token": self.access_token},
                               timeout=HTTP_TIMEOUT)
        return _transport.lire(r, geste)

    def get_profile(self) -> dict:
        return self._get(f"/{self.user_id}", "reading the profile",
                         fields=PROFILE_FIELDS)

    def get_recent_media(self, limit: int = 10) -> list[dict]:
        res = self._get(f"/{self.user_id}/media", "reading the posts",
                        fields=MEDIA_FIELDS, limit=limit)
        return res.get("data", []) or []

    def get_media_insights(self, media_id: str) -> dict[str, Any]:
        """Insights of a post — the metrics depend on its type.

        The type is READ first, never guessed: requesting a feed post's metrics
        on a reel fails the entire call."""
        meta = self._get(f"/{media_id}", "reading the post type",
                         fields="media_type,media_product_type")
        produit = str(meta.get("media_product_type") or "").upper()
        metriques = MEDIA_INSIGHT_METRICS.get(produit)
        if not metriques:
            raise ValueError(
                f"Insights not supported for this post: "
                f"media_product_type={produit or '?'}, "
                f"media_type={meta.get('media_type') or '?'} "
                f"(supported types: {', '.join(sorted(MEDIA_INSIGHT_METRICS))}).")
        res = self._get(f"/{media_id}/insights", "reading the insights",
                        metric=",".join(metriques))
        return flatten_insights(res)

    def get_account_insights(self, days: int = 30) -> dict[str, Any]:
        if not 1 <= days <= ACCOUNT_INSIGHTS_MAX_DAYS:
            raise ValueError(
                f"days must be between 1 and {ACCOUNT_INSIGHTS_MAX_DAYS} — that is the "
                f"API's maximum window on account insights.")
        until = int(time.time())
        res = self._get(f"/{self.user_id}/insights", "reading the account statistics",
                        metric=",".join(ACCOUNT_INSIGHT_METRICS), period="day",
                        metric_type="total_value",
                        since=until - days * 86_400, until=until)
        return flatten_insights(res)


def _corps(reponse) -> Any:
    """A response's JSON, or `None` — to query `jeton_mort` without raising."""
    try:
        return reponse.json()
    except ValueError:
        return None
