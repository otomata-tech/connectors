"""Brevo — transactional email (`/smtp/*`): sending, logs, events, templates.

Distinct from **campaigns** (`campaigns.py`, mass send to lists): here we
send a single message to one or a few recipients, directly or from a
template. Deliverability statistics (`events`) are the source of truth
for what became of an email (delivered / opened / hardBounce / spam…).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ._base import _BrevoBase


class TransactionalEmailMixin(_BrevoBase):

    # --- Sending -------------------------------------------------------------

    def send_email(
        self,
        to: List[Dict[str, str]],
        subject: Optional[str] = None,
        html_content: Optional[str] = None,
        text_content: Optional[str] = None,
        sender: Optional[Dict[str, str]] = None,
        template_id: Optional[int] = None,
        params: Optional[Dict[str, Any]] = None,
        cc: Optional[List[Dict[str, str]]] = None,
        bcc: Optional[List[Dict[str, str]]] = None,
        reply_to: Optional[Dict[str, str]] = None,
        attachment: Optional[List[Dict[str, str]]] = None,
        headers: Optional[Dict[str, str]] = None,
        tags: Optional[List[str]] = None,
        scheduled_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send a transactional email. Returns `{"messageId": …}`.

        Two mutually exclusive modes:
        - **template**: `template_id` (+ `params` for the variables) — `subject`
          and `sender` come from the template unless overridden;
        - **direct**: `subject` + `html_content` (or `text_content`) + `sender`.

        Args:
            to: `[{"email": …, "name": …}, …]` (max 99 recipients).
            sender: `{"email": …, "name": …}` or `{"id": <senderId>}`. The sender
                must be a verified sender of the account (see `list_senders`).
            attachment: `[{"url": …}]` or `[{"content": <base64>, "name": …}]`.
            scheduled_at: ISO 8601 UTC, up to 72 h in the future.
        """
        body = self._clean({
            "to": to, "subject": subject, "htmlContent": html_content,
            "textContent": text_content, "sender": sender, "templateId": template_id,
            "params": params, "cc": cc, "bcc": bcc, "replyTo": reply_to,
            "attachment": attachment, "headers": headers, "tags": tags,
            "scheduledAt": scheduled_at,
        })
        return self._request("POST", "/smtp/email", json=body)

    # --- Logs & statistics ----------------------------------------------------

    def list_transactional_emails(
        self,
        email: Optional[str] = None,
        template_id: Optional[int] = None,
        message_id: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        sort: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List sent transactional emails (metadata, `uuid` per email).

        Dates in `YYYY-MM-DD` format. Fetch a send's HTML body via
        `get_transactional_email_content(uuid)`.
        """
        params = self._clean({
            "email": email, "templateId": template_id, "messageId": message_id,
            "startDate": start_date, "endDate": end_date,
            "limit": min(limit, 500), "offset": offset, "sort": sort,
        })
        return self._request("GET", "/smtp/emails", params=params)

    def get_transactional_email_content(self, uuid: str) -> Dict[str, Any]:
        """HTML content of a sent transactional email (`uuid` seen in the logs)."""
        return self._request("GET", f"/smtp/emails/{uuid}")

    def transactional_events(
        self,
        limit: int = 50,
        offset: int = 0,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        days: Optional[int] = None,
        email: Optional[str] = None,
        event: Optional[str] = None,
        tags: Optional[str] = None,
        message_id: Optional[str] = None,
        template_id: Optional[int] = None,
        sort: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Deliverability event log — the source of truth per email.

        Args:
            event: `bounces` | `hardBounces` | `softBounces` | `delivered` |
                `spam` | `requests` | `opened` | `clicks` | `invalid` | `deferred`
                | `blocked` | `unsubscribed` | `error` | `loadedByProxy`.
            days: sliding window (days) — alternative to `start_date`/`end_date`.
        """
        params = self._clean({
            "limit": min(limit, 100), "offset": offset,
            "startDate": start_date, "endDate": end_date, "days": days,
            "email": email, "event": event, "tags": tags,
            "messageId": message_id, "templateId": template_id, "sort": sort,
        })
        return self._request("GET", "/smtp/statistics/events", params=params)

    def transactional_report(
        self,
        by_day: bool = False,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        days: Optional[int] = None,
        tag: Optional[str] = None,
        limit: int = 10,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Aggregated counters (requests, delivered, opens, clicks, bounces…).

        `by_day=False` (default) → one total over the period (`/aggregatedReport`).
        `by_day=True` → one row per day (`/reports`).
        """
        if by_day:
            params = self._clean({
                "limit": limit, "offset": offset, "startDate": start_date,
                "endDate": end_date, "days": days, "tag": tag,
            })
            return self._request("GET", "/smtp/statistics/reports", params=params)
        params = self._clean({
            "startDate": start_date, "endDate": end_date, "days": days, "tag": tag})
        return self._request(
            "GET", "/smtp/statistics/aggregatedReport", params=params or None)

    def list_blocked(
        self,
        domains: bool = False,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        senders: Optional[List[str]] = None,
        sort: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Blocked contacts (hard bounce, spam complaint, unsubscribe) or blocked domains.

        `domains=True` → `/smtp/blockedDomains` (simple list, no pagination).
        """
        if domains:
            return self._request("GET", "/smtp/blockedDomains")
        params = self._clean({
            "startDate": start_date, "endDate": end_date, "limit": limit,
            "offset": offset, "senders": senders, "sort": sort,
        })
        return self._request("GET", "/smtp/blockedContacts", params=params)

    # --- Templates ------------------------------------------------------------

    def list_templates(
        self, template_id: Optional[int] = None, active_only: Optional[bool] = None,
        limit: int = 50, offset: int = 0, sort: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Transactional templates. Pass `template_id` to fetch just one."""
        if template_id is not None:
            return self._request("GET", f"/smtp/templates/{int(template_id)}")
        params = self._clean({
            "templateStatus": active_only, "limit": min(limit, 1000),
            "offset": offset, "sort": sort,
        })
        return self._request("GET", "/smtp/templates", params=params)

    def create_template(
        self,
        template_name: str,
        subject: str,
        sender: Dict[str, str],
        html_content: Optional[str] = None,
        html_url: Optional[str] = None,
        reply_to: Optional[str] = None,
        to_field: Optional[str] = None,
        tag: Optional[str] = None,
        is_active: bool = True,
        attachment_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a transactional template. Returns `{"id": …}`.

        Args:
            sender: `{"email": …, "name": …}` or `{"id": <senderId>}`.
            html_content: HTML of the body. Alternative: `html_url` (remote page).
            to_field: recipient personalization, e.g. `{{contact.NOM}}`.
        """
        body = self._clean({
            "templateName": template_name, "subject": subject, "sender": sender,
            "htmlContent": html_content, "htmlUrl": html_url, "replyTo": reply_to,
            "toField": to_field, "tag": tag, "isActive": is_active,
            "attachmentUrl": attachment_url,
        })
        return self._request("POST", "/smtp/templates", json=body)

    def update_template(
        self,
        template_id: int,
        template_name: Optional[str] = None,
        subject: Optional[str] = None,
        sender: Optional[Dict[str, str]] = None,
        html_content: Optional[str] = None,
        html_url: Optional[str] = None,
        reply_to: Optional[str] = None,
        to_field: Optional[str] = None,
        tag: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Update a template (provided fields only). Empty body (204) on success."""
        body = self._clean({
            "templateName": template_name, "subject": subject, "sender": sender,
            "htmlContent": html_content, "htmlUrl": html_url, "replyTo": reply_to,
            "toField": to_field, "tag": tag, "isActive": is_active,
        })
        return self._request("PUT", f"/smtp/templates/{int(template_id)}", json=body)
