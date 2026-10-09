"""Calendar memberships and webhooks.

Composed into `LumaClient`, which provides the transport and the checks.

- ⚠️ Approving a member of a PAID tier captures their payment; declining
  cancels any active subscription. `skip_payment=True` on `add_member` is for
  payment handled outside Luma.
- A webhook receives the calendar's notifications at a public URL; `status`
  pauses or resumes it without deleting it.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..const import (MEMBERSHIP_SET_STATUSES, WEBHOOK_EVENT_TYPES,
                     WEBHOOK_STATUSES)


class _MembershipsMixin:
    """Membership tiers and their members."""

    def list_membership_tiers(self, *, limit: Optional[int] = None,
                              cursor: Optional[str] = None) -> Any:
        """GET /v1/memberships/tiers/list — hidden tiers included (`is_hidden`)."""
        return self._list("/v1/memberships/tiers/list", limit, cursor)

    def add_member(self, email: str, membership_tier_id: str, *,
                   skip_payment: Optional[bool] = None,
                   registration_answers: Optional[List[Dict[str, Any]]] = None) -> Any:
        """POST /v1/memberships/members/add — add a person to a tier."""
        return self._post("/v1/memberships/members/add", self._body(
            {"email": self._need(email, "email"),
             "membership_tier_id": self._need(membership_tier_id,
                                              "membership_tier_id")},
            optional={"skip_payment": skip_payment,
                      "registration_answers": registration_answers}))

    def update_member_status(self, user_id: str, status: str) -> Any:
        """POST /v1/memberships/members/update-status — `approved` (captures a
        paid tier's payment) or `declined` (cancels the subscription)."""
        self._need(status, "status")
        self._check_choice("status", status, MEMBERSHIP_SET_STATUSES)
        return self._post("/v1/memberships/members/update-status", {
            "user_id": self._need(user_id, "user_id"), "status": status})


class _WebhooksMixin:
    """Webhook endpoints of the calendar."""

    def list_webhooks(self, *, limit: Optional[int] = None,
                      cursor: Optional[str] = None) -> Any:
        """GET /v1/webhooks/list."""
        return self._list("/v1/webhooks/list", limit, cursor)

    def get_webhook(self, webhook_id: str) -> Any:
        """GET /v2/webhooks/get."""
        return self._get("/v2/webhooks/get",
                         {"id": self._need(webhook_id, "webhook_id")})

    def create_webhook(self, url: str, event_types: List[str]) -> Any:
        """POST /v2/webhooks/create — `event_types`: names from
        `WEBHOOK_EVENT_TYPES`, or `["*"]` for all."""
        self._need(event_types, "event_types")
        self._check_choice("event_types", event_types, WEBHOOK_EVENT_TYPES)
        return self._post("/v2/webhooks/create", {
            "url": self._need(url, "url"), "event_types": list(event_types)})

    def update_webhook(self, webhook_id: str, *,
                       event_types: Optional[List[str]] = None,
                       status: Optional[str] = None) -> Any:
        """POST /v2/webhooks/update — change the event types, or pause/resume."""
        self._check_choice("event_types", event_types, WEBHOOK_EVENT_TYPES)
        self._check_choice("status", status, WEBHOOK_STATUSES)
        if not event_types and not status:
            raise ValueError("`event_types` or `status` is required.")
        return self._post("/v2/webhooks/update", self._body(
            {"id": self._need(webhook_id, "webhook_id")},
            optional={"event_types": list(event_types) if event_types else None,
                      "status": status}))

    def delete_webhook(self, webhook_id: str) -> Any:
        """POST /v1/webhooks/delete."""
        return self._post("/v1/webhooks/delete",
                          {"id": self._need(webhook_id, "webhook_id")})
