"""Brevo — email campaigns (mass send to lists/segments).

Distinct from transactional (`email.py`): a campaign targets entire lists
and is scheduled. **The API exposes design and measurement here, not the
trigger**: `sendNow`, switching the status to `sent` and deletion are
deliberately not wrapped (an unlucky LLM call would send to the whole
base). You create/edit a draft, send yourself a test, read the stats; the
send is triggered from the Brevo UI.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ._base import _BrevoBase


class CampaignsMixin(_BrevoBase):

    def list_campaigns(
        self,
        type: Optional[str] = None,
        status: Optional[str] = None,
        statistics: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        sort: Optional[str] = None,
        exclude_html_content: bool = True,
    ) -> Dict[str, Any]:
        """List email campaigns.

        Args:
            type: `classic` | `trigger`.
            status: `suspended` | `archive` | `sent` | `queued` | `draft` |
                `inProcess` | `replicate` | `replicateTemplate`.
            statistics: `globalStats` | `linksStats` | `statsByDomain` |
                `statsByDevice` | `statsByBrowser` — enriches each campaign.
            exclude_html_content: `True` (default) makes the response much lighter.
        """
        params = self._clean({
            "type": type, "status": status, "statistics": statistics,
            "startDate": start_date, "endDate": end_date,
            "limit": min(limit, 100), "offset": offset, "sort": sort,
            "excludeHtmlContent": exclude_html_content,
        })
        return self._request("GET", "/emailCampaigns", params=params)

    def get_campaign(
        self, campaign_id: int, statistics: Optional[str] = None,
        exclude_html_content: bool = True,
    ) -> Dict[str, Any]:
        """A campaign's details, with its stats if `statistics` is provided."""
        params = self._clean({
            "statistics": statistics, "excludeHtmlContent": exclude_html_content})
        return self._request(
            "GET", f"/emailCampaigns/{int(campaign_id)}", params=params or None)

    def create_campaign(
        self,
        name: str,
        sender: Dict[str, str],
        subject: Optional[str] = None,
        html_content: Optional[str] = None,
        html_url: Optional[str] = None,
        template_id: Optional[int] = None,
        recipients: Optional[Dict[str, Any]] = None,
        scheduled_at: Optional[str] = None,
        reply_to: Optional[str] = None,
        preview_text: Optional[str] = None,
        tag: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a campaign (draft if `scheduled_at` is omitted). Returns `{"id": …}`.

        Args:
            sender: `{"email": …, "name": …}` or `{"id": <senderId>}` — verified sender.
            recipients: `{"listIds": [1,2], "exclusionListIds": [3], "segmentIds": [4]}`.
            scheduled_at: ISO 8601 UTC. **Actually schedules the send.** Omit to
                stay in draft.
            template_id: start from a template instead of `html_content`.
        """
        body = self._clean({
            "name": name, "sender": sender, "subject": subject,
            "htmlContent": html_content, "htmlUrl": html_url,
            "templateId": template_id, "recipients": recipients,
            "scheduledAt": scheduled_at, "replyTo": reply_to,
            "previewText": preview_text, "tag": tag, "params": params,
        })
        return self._request("POST", "/emailCampaigns", json=body)

    def update_campaign(self, campaign_id: int, **fields: Any) -> Dict[str, Any]:
        """Update a campaign **not yet sent** (provided fields only).

        Accepts the same keys as `create_campaign`, in Brevo camelCase
        (`htmlContent`, `scheduledAt`, `recipients`…). Empty body (204) on success.
        """
        return self._request(
            "PUT", f"/emailCampaigns/{int(campaign_id)}", json=self._clean(fields))

    def send_campaign_test(self, campaign_id: int,
                           email_to: List[str]) -> Dict[str, Any]:
        """Send a test of the campaign to the given addresses.

        These addresses must exist as contacts of the Brevo account. Does
        **not** send the campaign to its real recipients.
        """
        return self._request("POST", f"/emailCampaigns/{int(campaign_id)}/sendTest",
                             json={"emailTo": email_to})

    def campaign_ab_test_result(self, campaign_id: int) -> Dict[str, Any]:
        """A/B test result (winner, criterion, stats per variant)."""
        return self._request(
            "GET", f"/emailCampaigns/{int(campaign_id)}/abTestCampaignResult")

    def campaign_shared_url(self, campaign_id: int) -> Dict[str, Any]:
        """Public share URL (browser view) of a sent campaign."""
        return self._request("GET", f"/emailCampaigns/{int(campaign_id)}/sharedUrl")
