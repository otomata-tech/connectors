"""Ticket types and coupons of an event, and coupons of a calendar.

Composed into `LumaClient`, which provides the transport and the checks.

- Paid ticket types need a Stripe account connected to the calendar. Prices
  are in the currency's minor unit (`cents: 2500` = 25.00).
- Deleting a ticket type is a soft delete: its holders keep their tickets,
  and the last visible type cannot be deleted.
- A coupon's TERMS (its discount) cannot change after creation; only its
  remaining count and validity window can. A coupon restricted to a HIDDEN
  ticket type is an unlock code. Do not reuse a code on an event and on its
  calendar.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..const import COUPON_DISCOUNT_TYPES, TICKET_PRICE_TYPES


def _discount(discount: Dict[str, Any]) -> Dict[str, Any]:
    """`{discount_type: "percent", percent_off}` or `{discount_type: "amount",
    cents_off, currency}`, checked before it leaves."""
    if not isinstance(discount, dict) or not discount:
        raise ValueError("`discount` is required: {discount_type: 'percent', "
                         "percent_off} or {discount_type: 'amount', cents_off, "
                         "currency}.")
    kind = discount.get("discount_type")
    if kind not in COUPON_DISCOUNT_TYPES:
        raise ValueError(f"`discount.discount_type` invalid: {kind!r}. Accepted "
                         "values: " + ", ".join(repr(k) for k in COUPON_DISCOUNT_TYPES))
    needed = ("percent_off",) if kind == "percent" else ("cents_off", "currency")
    for key in needed:
        if discount.get(key) in (None, ""):
            raise ValueError(f"`discount.{key}` is required for a "
                             f"{kind!r} discount.")
    return discount


class _TicketsMixin:
    """Ticket types and coupons."""

    # --- ticket types -------------------------------------------------------

    def list_ticket_types(self, event_id: str, *,
                          include_hidden: Optional[bool] = None) -> Any:
        """GET /v1/events/ticket-types/list — the event's ticket types."""
        return self._get("/v1/events/ticket-types/list", {
            "event_id": self._need(event_id, "event_id"),
            "include_hidden": include_hidden})

    def get_ticket_type(self, ticket_type_id: str) -> Any:
        """GET /v1/events/ticket-types/get — one ticket type (`ttype-…`)."""
        return self._get("/v1/events/ticket-types/get", {
            "event_ticket_type_id": self._need(ticket_type_id, "ticket_type_id")})

    def create_ticket_type(self, event_id: str, fields: Dict[str, Any]) -> Any:
        """POST /v1/events/ticket-types/create — `fields`: `name` (≤ 30) and
        `type` (`free`|`paid`) required; `cents` + `currency` for a paid
        type; require_approval, is_hidden, description, max_capacity,
        valid_start_at, valid_end_at, is_flexible + min_cents."""
        self._need(fields, "fields")
        self._need(fields.get("name"), "fields.name")
        self._need(fields.get("type"), "fields.type")
        self._check_choice("fields.type", fields.get("type"), TICKET_PRICE_TYPES)
        return self._post("/v1/events/ticket-types/create", self._body(
            {"event_id": self._need(event_id, "event_id")}, fields))

    def update_ticket_type(self, ticket_type_id: str,
                           fields: Dict[str, Any]) -> Any:
        """POST /v1/events/ticket-types/update — only the given keys change."""
        self._need(fields, "fields")
        self._check_choice("fields.type", fields.get("type"), TICKET_PRICE_TYPES)
        return self._post("/v1/events/ticket-types/update", self._body(
            {"event_ticket_type_id": self._need(ticket_type_id, "ticket_type_id")},
            fields))

    def delete_ticket_type(self, ticket_type_id: str) -> Any:
        """POST /v1/events/ticket-types/delete — soft delete."""
        return self._post("/v1/events/ticket-types/delete", {
            "event_ticket_type_id": self._need(ticket_type_id, "ticket_type_id")})

    # --- coupons ------------------------------------------------------------

    def list_event_coupons(self, event_id: str, *, limit: Optional[int] = None,
                           cursor: Optional[str] = None) -> Any:
        """GET /v1/events/coupons/list — coupons of an event."""
        return self._list("/v1/events/coupons/list", limit, cursor,
                          {"event_id": self._need(event_id, "event_id")})

    def list_calendar_coupons(self, *, limit: Optional[int] = None,
                              cursor: Optional[str] = None) -> Any:
        """GET /v1/calendars/coupons/list — coupons valid on every event the
        calendar manages."""
        return self._list("/v1/calendars/coupons/list", limit, cursor)

    def create_coupon(self, code: str, discount: Dict[str, Any], *,
                      event_id: Optional[str] = None,
                      ticket_type_id: Optional[str] = None,
                      remaining_count: Optional[int] = None,
                      valid_start_at: Optional[str] = None,
                      valid_end_at: Optional[str] = None) -> Any:
        """POST /v1/events/coupons/create with `event_id`, else
        /v1/calendars/coupons/create (calendar-wide). `code` ≤ 20 characters,
        case-insensitive; `remaining_count` 1000000 = unlimited.
        `ticket_type_id` (event coupons only) restricts it to a type."""
        if ticket_type_id and not event_id:
            raise ValueError("`ticket_type_id` restricts an EVENT coupon: "
                             "`event_id` is required with it.")
        optional = {"remaining_count": remaining_count,
                    "valid_start_at": valid_start_at, "valid_end_at": valid_end_at}
        required: Dict[str, Any] = {"code": self._need(code, "code"),
                                    "discount": _discount(discount)}
        if event_id:
            required["event_id"] = event_id
            optional["event_ticket_type_id"] = ticket_type_id
            return self._post("/v1/events/coupons/create",
                              self._body(required, optional=optional))
        return self._post("/v1/calendars/coupons/create",
                          self._body(required, optional=optional))

    def update_coupon(self, code: str, *, event_id: Optional[str] = None,
                      remaining_count: Optional[int] = None,
                      valid_start_at: Optional[str] = None,
                      valid_end_at: Optional[str] = None) -> Any:
        """POST /v1/events/coupons/update with `event_id`, else
        /v1/calendars/coupons/update. The discount itself cannot change."""
        optional = {"remaining_count": remaining_count,
                    "valid_start_at": valid_start_at, "valid_end_at": valid_end_at}
        required: Dict[str, Any] = {"code": self._need(code, "code")}
        if event_id:
            required["event_id"] = event_id
            return self._post("/v1/events/coupons/update",
                              self._body(required, optional=optional))
        return self._post("/v1/calendars/coupons/update",
                          self._body(required, optional=optional))
