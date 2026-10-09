"""Guests of an event, and blasts (emails to its guests).

Composed into `LumaClient`, which provides the transport and the checks.

What reaches people outside the organization, and how:

- `add_guests` registers people directly (default status `approved`, one
  ticket of the default type). Whether they are emailed follows `send_email`.
- `send_invites` emails an invitation they can accept (and texts it when a
  phone number is linked to their Luma account).
- `update_guest_status` emails the guest unless `send_email=False`;
  `should_refund` refunds a paid guest moved out of `approved`.
- `create_blast` emails the event's guests now or at `scheduled_for`, counts
  against the calendar's send limit (10 blasts per 5 minutes), and cannot be
  recalled once sent — deleting a SENT blast only removes the post from the
  event page.

Guests are added and invited in the BACKGROUND: the response lists the people
that will be skipped (unsubscribed, removed by an admin, or who blocked the
calendar), not the final state.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..const import (BLAST_RECIPIENT_STATUSES, GUEST_ADD_STATUSES,
                     GUEST_LIST_STATUSES, GUEST_SET_STATUSES,
                     GUEST_SORT_COLUMNS, SORT_DIRECTIONS)


def _people(guests: List[Dict[str, Any]], name: str) -> List[Dict[str, Any]]:
    """A non-empty list of `{email, …}` objects."""
    if not guests or not isinstance(guests, list):
        raise ValueError(f"`{name}` must be a non-empty list of {{email, name?}}.")
    for i, g in enumerate(guests):
        if not isinstance(g, dict) or not g.get("email"):
            raise ValueError(f"`{name}[{i}]` must carry an `email`.")
    return guests


class _GuestsMixin:
    """Guests of an event."""

    def list_guests(self, event_id: str, *,
                    approval_status: Optional[str] = None,
                    sort_column: Optional[str] = None,
                    sort_direction: Optional[str] = None,
                    limit: Optional[int] = None,
                    cursor: Optional[str] = None) -> Any:
        """GET /v1/events/guests/list — registered and invited guests with
        their tickets; order-level detail is on `get_guest`."""
        self._check_choice("approval_status", approval_status, GUEST_LIST_STATUSES)
        self._check_choice("sort_column", sort_column, GUEST_SORT_COLUMNS)
        self._check_choice("sort_direction", sort_direction, SORT_DIRECTIONS)
        return self._list("/v1/events/guests/list", limit, cursor, {
            "event_id": self._need(event_id, "event_id"),
            "approval_status": approval_status, "sort_column": sort_column,
            "sort_direction": sort_direction})

    def get_guest(self, event_id: str, guest: str) -> Any:
        """GET /v1/events/guests/get — one guest, with ticket orders (and
        coupon applied). `guest`: guest id (`gst-…`), ticket key, guest key
        (`g-…`) or email."""
        return self._get("/v1/events/guests/get", {
            "event_id": self._need(event_id, "event_id"),
            "id": self._need(guest, "guest")})

    def add_guests(self, event_id: str, guests: List[Dict[str, Any]], *,
                   approval_status: Optional[str] = None,
                   ticket_type_ids: Optional[List[str]] = None,
                   send_email: Optional[bool] = None) -> Any:
        """POST /v1/events/guests/add — `guests`: `[{email, name?,
        registration_answers?}]`. `ticket_type_ids` gives each guest one
        ticket of every listed type (default: one of the default type)."""
        self._check_choice("approval_status", approval_status, GUEST_ADD_STATUSES)
        tickets = ([{"event_ticket_type_id": t} for t in ticket_type_ids]
                   if ticket_type_ids else None)
        return self._post("/v1/events/guests/add", self._body(
            {"event_id": self._need(event_id, "event_id"),
             "guests": _people(guests, "guests")},
            optional={"approval_status": approval_status, "tickets": tickets,
                      "send_email": send_email}))

    def update_guest_status(self, event_id: str, guest: str, status: str, *,
                            should_refund: Optional[bool] = None,
                            send_email: Optional[bool] = None,
                            message: Optional[str] = None) -> Any:
        """POST /v1/events/guests/update-status — `approved` (going),
        `declined`, `pending_approval` or `waitlist`. `guest` as in
        `get_guest`. `message` adds a personal note to the email."""
        self._need(status, "status")
        self._check_choice("status", status, GUEST_SET_STATUSES)
        return self._post("/v1/events/guests/update-status", self._body(
            {"event_id": self._need(event_id, "event_id"),
             "guest_id": self._need(guest, "guest"), "status": status},
            optional={"should_refund": should_refund, "send_email": send_email,
                      "message": message}))

    def update_guest_tickets(self, event_id: str, guest: str, *,
                             add_ticket_type_ids: Optional[List[str]] = None,
                             remove_ticket_ids: Optional[List[str]] = None,
                             should_refund: Optional[bool] = None,
                             send_email: Optional[bool] = None) -> Any:
        """POST /v1/events/guests/update-tickets — added tickets are
        complimentary and may exceed capacity; removed ones are invalidated,
        and refunded only with `should_refund`."""
        if not add_ticket_type_ids and not remove_ticket_ids:
            raise ValueError(
                "`add_ticket_type_ids` or `remove_ticket_ids` is required.")
        adds = ([{"event_ticket_type_id": t} for t in add_ticket_type_ids]
                if add_ticket_type_ids else None)
        return self._post("/v1/events/guests/update-tickets", self._body(
            {"event_id": self._need(event_id, "event_id"),
             "guest_id": self._need(guest, "guest")},
            optional={"tickets_to_add": adds,
                      "ticket_ids_to_remove": remove_ticket_ids or None,
                      "should_refund": should_refund, "send_email": send_email}))

    def send_invites(self, event_id: str, guests: List[Dict[str, Any]], *,
                     message: Optional[str] = None) -> Any:
        """POST /v1/events/guests/send-invites — ⚠️ emails (and texts) each
        person an invitation. `guests`: `[{email, name?}]`."""
        return self._post("/v1/events/guests/send-invites", self._body(
            {"event_id": self._need(event_id, "event_id"),
             "guests": _people(guests, "guests")},
            optional={"message": message}))


class _BlastsMixin:
    """Emails to an event's guests."""

    @staticmethod
    def _recipients(groups: Optional[List[Dict[str, Any]]]) -> Optional[List]:
        if groups is None:
            return None
        if not isinstance(groups, list) or not groups:
            raise ValueError("`recipient_groups` must be a non-empty list of "
                             "{status, event_ticket_type_id?}.")
        for i, g in enumerate(groups):
            status = g.get("status") if isinstance(g, dict) else None
            if status not in BLAST_RECIPIENT_STATUSES:
                raise ValueError(
                    f"`recipient_groups[{i}].status` invalid: {status!r}. "
                    "Accepted values: "
                    + ", ".join(repr(s) for s in BLAST_RECIPIENT_STATUSES))
        return groups

    def list_blasts(self, event_id: str) -> Any:
        """GET /v1/events/blasts/list — newest first; sent ones carry
        `recipient_count` and `email_open_count`."""
        return self._get("/v1/events/blasts/list",
                         {"event_id": self._need(event_id, "event_id")})

    def get_blast(self, blast_id: str) -> Any:
        """GET /v1/events/blasts/get — one blast (`ep-…`)."""
        return self._get("/v1/events/blasts/get",
                         {"blast_id": self._need(blast_id, "blast_id")})

    def create_blast(self, event_id: str, content_md: str, *,
                     subject: Optional[str] = None,
                     recipient_groups: Optional[List[Dict[str, Any]]] = None,
                     scheduled_for: Optional[str] = None) -> Any:
        """POST /v1/events/blasts/create — ⚠️ emails the guests, now or at
        `scheduled_for` (ISO 8601, future). Default recipients: going
        (`approved`) guests of every ticket type."""
        return self._post("/v1/events/blasts/create", self._body(
            {"event_id": self._need(event_id, "event_id"),
             "content_md": self._need(content_md, "content_md")},
            optional={"subject": subject,
                      "recipient_groups": self._recipients(recipient_groups),
                      "scheduled_for": scheduled_for}))

    def update_blast(self, blast_id: str, *, content_md: Optional[str] = None,
                     subject: Optional[str] = None,
                     recipient_groups: Optional[List[Dict[str, Any]]] = None,
                     scheduled_for: Optional[str] = None) -> Any:
        """POST /v1/events/blasts/update — before sending, everything can
        change; once sent, only `subject` and `content_md`, which updates the
        event page post, not the emails already delivered."""
        return self._post("/v1/events/blasts/update", self._body(
            {"blast_id": self._need(blast_id, "blast_id")},
            optional={"content_md": content_md, "subject": subject,
                      "recipient_groups": self._recipients(recipient_groups),
                      "scheduled_for": scheduled_for}))

    def delete_blast(self, blast_id: str) -> Any:
        """POST /v1/events/blasts/delete — cancels a scheduled blast; for a
        sent one, removes the post but not the delivered emails."""
        return self._post("/v1/events/blasts/delete",
                          {"blast_id": self._need(blast_id, "blast_id")})
