"""Instagram Business — content publishing & insights via the Meta Graph API.

Target: an **Instagram Business or Creator** account linked to a Facebook Page.
Covers **publishing** (image / reel / carousel / story) and **insights**
(account + media) — NOT messaging (DMs go through the Unipile connector
`instagram_*`).

Auth = **access token** (long-lived user/page token, scopes `instagram_basic`,
`instagram_content_publish`, `instagram_manage_insights`) + the **IG user id** of the
business account (the numeric "IG User ID", ≠ the Facebook Page identifier).
Both passed to the constructor.

Publishing = a 2-step Graph API flow: first a media **container** is created
(`POST /{ig-user-id}/media`) then it is **published** (`POST /{ig-user-id}/
media_publish`). Images are published synchronously; video / reel
is processed asynchronously on Meta's side → create the container, **poll the
status** (`status_code=FINISHED`) before publishing.

Docs: https://developers.facebook.com/docs/instagram-platform/content-publishing

Requires: requests
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

from ..common.credentials import require
from ..common import raise_for_upstream


class InstagramBusinessClient:
    """Graph API client — publishing & insights for an Instagram Business account."""

    DEFAULT_API_VERSION = "v21.0"

    def __init__(
        self,
        access_token: Optional[str] = None,
        ig_user_id: Optional[str] = None,
        api_version: Optional[str] = None,
    ):
        """Initialize the client.

        Args:
            access_token: Graph API token.
            ig_user_id: IG User ID of the business account.
            api_version: Graph API version (default `v21.0`).
        """
        self.access_token = require(access_token, "IG_BUSINESS_ACCESS_TOKEN")
        self.ig_user_id = str(require(ig_user_id, "IG_BUSINESS_USER_ID"))
        self.api_version = api_version or self.DEFAULT_API_VERSION
        self.base_url = f"https://graph.facebook.com/{self.api_version}"
        self.session = requests.Session()

    # --- transport ----------------------------------------------------------

    def _request(self, method: str, path: str, *, params: Optional[Dict[str, Any]] = None,
                 data: Optional[Dict[str, Any]] = None) -> Any:
        params = dict(params or {})
        params["access_token"] = self.access_token
        url = f"{self.base_url}/{path.lstrip('/')}"
        resp = self.session.request(method, url, params=params, data=data, timeout=60)
        raise_for_upstream(resp, service="instagram")
        return resp.json() if resp.content else {}

    # --- containers (step 1 of publishing) ----------------------------------

    def create_media_container(
        self,
        *,
        image_url: Optional[str] = None,
        video_url: Optional[str] = None,
        media_type: Optional[str] = None,
        caption: Optional[str] = None,
        location_id: Optional[str] = None,
        user_tags: Optional[List[Dict[str, Any]]] = None,
        is_carousel_item: bool = False,
        children: Optional[List[str]] = None,
        share_to_feed: Optional[bool] = None,
        thumb_offset: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Create a media container (step 1). Returns `{"id": <creation_id>}`.

        `media_type`: None (single image), `REELS` (video/reel), `STORIES`
        (story), `CAROUSEL` (album, requires `children` = ids of child
        containers). For a carousel child, set `is_carousel_item=True`.
        """
        data: Dict[str, Any] = {}
        if image_url:
            data["image_url"] = image_url
        if video_url:
            data["video_url"] = video_url
        if media_type:
            data["media_type"] = media_type
        if caption is not None:
            data["caption"] = caption
        if location_id:
            data["location_id"] = location_id
        if user_tags is not None:
            import json
            data["user_tags"] = json.dumps(user_tags)
        if is_carousel_item:
            data["is_carousel_item"] = "true"
        if children:
            data["children"] = ",".join(children)
        if share_to_feed is not None:
            data["share_to_feed"] = "true" if share_to_feed else "false"
        if thumb_offset is not None:
            data["thumb_offset"] = thumb_offset
        return self._request("POST", f"{self.ig_user_id}/media", data=data)

    def container_status(self, creation_id: str) -> Dict[str, Any]:
        """Status of a media container. `status_code` ∈ EXPIRED | ERROR |
        FINISHED | IN_PROGRESS | PUBLISHED. Publish only on FINISHED."""
        return self._request(
            "GET", str(creation_id),
            params={"fields": "status_code,status"})

    # --- publishing (step 2) ------------------------------------------------

    def publish_container(self, creation_id: str) -> Dict[str, Any]:
        """Publish a previously created container. Returns `{"id": <media_id>}`."""
        return self._request(
            "POST", f"{self.ig_user_id}/media_publish",
            data={"creation_id": str(creation_id)})

    def publish_image(self, image_url: str, caption: Optional[str] = None) -> Dict[str, Any]:
        """Synchronous shortcut: create the image container THEN publish it.
        Returns the published media `{"id": <media_id>}`."""
        container = self.create_media_container(image_url=image_url, caption=caption)
        return self.publish_container(container["id"])

    # --- media reading ------------------------------------------------------

    def list_media(self, limit: int = 25, fields: Optional[str] = None) -> Dict[str, Any]:
        """List the account's published media (paginated)."""
        return self._request(
            "GET", f"{self.ig_user_id}/media",
            params={
                "limit": limit,
                "fields": fields or "id,caption,media_type,media_url,permalink,timestamp",
            })

    def get_media(self, media_id: str, fields: Optional[str] = None) -> Dict[str, Any]:
        """Detail of a published media."""
        return self._request(
            "GET", str(media_id),
            params={
                "fields": fields or
                "id,caption,media_type,media_url,permalink,timestamp,"
                "like_count,comments_count",
            })

    # --- insights -----------------------------------------------------------

    def account_insights(
        self,
        metrics: List[str],
        period: str = "day",
        *,
        metric_type: Optional[str] = None,
        since: Optional[int] = None,
        until: Optional[int] = None,
    ) -> Dict[str, Any]:
        """**Account** insights (`GET /{ig-user-id}/insights`).

        Args:
            metrics: metrics, e.g. `reach`, `impressions`, `profile_views`,
                `follower_count`, `accounts_engaged`.
            period: `day` | `week` | `days_28` | `lifetime`.
            metric_type: `total_value` for modern metrics (reach…).
            since/until: optional epoch (UNIX) bounds.
        """
        params: Dict[str, Any] = {"metric": ",".join(metrics), "period": period}
        if metric_type:
            params["metric_type"] = metric_type
        if since is not None:
            params["since"] = since
        if until is not None:
            params["until"] = until
        return self._request("GET", f"{self.ig_user_id}/insights", params=params)

    def media_insights(self, media_id: str, metrics: Optional[List[str]] = None) -> Dict[str, Any]:
        """Insights of a published **media** (`GET /{ig-media-id}/insights`).
        Default metrics: reach, likes, comments, saved, shares."""
        ms = metrics or ["reach", "likes", "comments", "saved", "shares"]
        return self._request(
            "GET", f"{media_id}/insights",
            params={"metric": ",".join(ms)})
