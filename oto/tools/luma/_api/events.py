"""Events, their submission to a calendar, hosts, and cancellation.

Composed into `LumaClient`, which provides the transport and the checks.

- An event is managed by ONE calendar. A calendar also LISTS events managed
  elsewhere (`access: "view"`): those come back with their location reduced
  to the city and without host-only fields. Private events managed elsewhere
  are never listed.
- List entries omit the description: read it with `get_event`.
- ⚠️ `cancel_event` is irreversible: it notifies every guest, refunds when
  asked, and DELETES the event. It spends a token from
  `request_event_cancellation`, valid 15 minutes. For an event with paid
  guests, `should_refund` must be given.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..const import (CALENDAR_EVENT_SORT_COLUMNS, CALENDAR_EVENT_STATUSES,
                     EVENT_ACCESS, EVENT_PLATFORMS, HOST_ACCESS_LEVELS,
                     SORT_DIRECTIONS, SUBMISSION_MODES)


class _EventsMixin:
    """Events of a calendar."""

    # --- read ---------------------------------------------------------------

    def get_event(self, event_id: str) -> Any:
        """GET /v1/events/get — full detail for an event the key manages
        (hosts, guest counts by status, registration questions); the public
        fields only for an event it merely views."""
        return self._get("/v1/events/get",
                         {"event_id": self._need(event_id, "event_id")})

    def list_events(self, *, after: Optional[str] = None,
                    before: Optional[str] = None,
                    status: Optional[str] = None,
                    access: Optional[List[str]] = None,
                    platforms: Optional[List[str]] = None,
                    sort_column: Optional[str] = None,
                    sort_direction: Optional[str] = None,
                    limit: Optional[int] = None,
                    cursor: Optional[str] = None) -> Any:
        """GET /v1/calendars/events/list — events of the calendar.

        `after`/`before` bound the start date (ISO 8601). `status`: `approved`
        (default) or `pending` submissions. `access`: `manage` (default) and/or
        `view`. `platforms`: `luma` (default) and/or `external`."""
        self._check_choice("status", status, CALENDAR_EVENT_STATUSES)
        self._check_choice("access", access, EVENT_ACCESS)
        self._check_choice("platforms", platforms, EVENT_PLATFORMS)
        self._check_choice("sort_column", sort_column, CALENDAR_EVENT_SORT_COLUMNS)
        self._check_choice("sort_direction", sort_direction, SORT_DIRECTIONS)
        return self._list("/v1/calendars/events/list", limit, cursor, {
            "after": after, "before": before, "status": status,
            "access": list(access) if access else None,
            "platforms": list(platforms) if platforms else None,
            "sort_column": sort_column, "sort_direction": sort_direction})

    def lookup_event(self, *, event_id: Optional[str] = None,
                     url: Optional[str] = None,
                     platform: Optional[str] = None) -> Any:
        """GET /v1/calendars/events/lookup — whether an event (a Luma event by
        id, or any event by URL) is already on the calendar."""
        if not event_id and not url:
            raise ValueError("`event_id` or `url` is required.")
        self._check_choice("platform", platform, EVENT_PLATFORMS)
        return self._get("/v1/calendars/events/lookup", {
            "event_id": event_id, "url": url, "platform": platform})

    # --- write --------------------------------------------------------------

    def create_event(self, fields: Dict[str, Any]) -> Any:
        """POST /v1/events/create — `name`, `start_at` (ISO 8601) and
        `timezone` (IANA) are required. Other keys: end_at, description_md,
        geo_address_json, meeting_url, create_meeting, max_capacity,
        visibility, registration_open, registration_questions, ticket_types,
        cover_url, slug, … as documented by Luma."""
        self._need(fields, "fields")
        for key in ("name", "start_at", "timezone"):
            self._need(fields.get(key), f"fields.{key}")
        return self._post("/v1/events/create", dict(fields))

    def update_event(self, event_id: str, fields: Dict[str, Any]) -> Any:
        """POST /v1/events/update — only the given keys change.
        `suppress_email` / `suppress_notifications` keep guests from being
        told about the change."""
        return self._post("/v1/events/update", self._body(
            {"event_id": self._need(event_id, "event_id")},
            self._need(fields, "fields")))

    def request_event_cancellation(self, event_id: str) -> Any:
        """POST /v1/events/cancel/request — a `cancellation_token` (15 minutes)
        and whether the event has paid guests. Changes nothing by itself."""
        return self._post("/v1/events/cancel/request",
                          {"event_id": self._need(event_id, "event_id")})

    def cancel_event(self, event_id: str, cancellation_token: str, *,
                     should_refund: Optional[bool] = None) -> Any:
        """POST /v1/events/cancel — ⚠️ IRREVERSIBLE: guests are notified, the
        event is deleted, refunds follow `should_refund` (required for an
        event with paid guests)."""
        return self._post("/v1/events/cancel", self._body(
            {"event_id": self._need(event_id, "event_id"),
             "cancellation_token": self._need(cancellation_token,
                                              "cancellation_token")},
            optional={"should_refund": should_refund}))

    # --- calendar submissions ----------------------------------------------

    def add_event_to_calendar(self, fields: Dict[str, Any], *,
                              submission_mode: Optional[str] = None) -> Any:
        """POST /v1/calendars/events/add — add or submit an existing event.

        A Luma event: `{platform: "luma", event_id}`. An external one:
        `{platform: "external", url, name, start_at, duration_interval
        (ISO 8601, e.g. PT2H), timezone, geo_address_json?, host?}`. Added by
        a calendar manager, it is approved at once unless
        `submission_mode="pending"`."""
        self._need(fields, "fields")
        self._check_choice("platform", fields.get("platform"), EVENT_PLATFORMS)
        self._need(fields.get("platform"), "fields.platform")
        self._check_choice("submission_mode", submission_mode, SUBMISSION_MODES)
        return self._post("/v1/calendars/events/add", self._body(
            {}, fields, {"submission_mode": submission_mode}))

    def approve_event(self, calendar_event_id: str) -> Any:
        """POST /v1/calendars/events/approve — accept a pending submission; it
        becomes visible and the submitter is notified."""
        return self._post("/v1/calendars/events/approve", {
            "calendar_event_id": self._need(calendar_event_id, "calendar_event_id")})

    def reject_event(self, calendar_event_id: str, *,
                     message: Optional[str] = None,
                     notify_submitter: Optional[bool] = None) -> Any:
        """POST /v1/calendars/events/reject — refuse a pending submission. The
        submitter is emailed only with `notify_submitter=True` or a
        `message`."""
        return self._post("/v1/calendars/events/reject", self._body(
            {"calendar_event_id": self._need(calendar_event_id, "calendar_event_id")},
            optional={"message": message, "notify_submitter": notify_submitter}))

    # --- hosts --------------------------------------------------------------

    def add_host(self, event_id: str, email: str, *,
                 access_level: Optional[str] = None,
                 is_visible: Optional[bool] = None,
                 name: Optional[str] = None) -> Any:
        """POST /v1/events/hosts/add — a co-host (`manager`) or check-in staff
        (`check-in`); `none` lists them without access."""
        self._check_choice("access_level", access_level, HOST_ACCESS_LEVELS)
        return self._post("/v1/events/hosts/add", self._body(
            {"event_id": self._need(event_id, "event_id"),
             "email": self._need(email, "email")},
            optional={"access_level": access_level, "is_visible": is_visible,
                      "name": name}))

    def update_host(self, event_id: str, email: str, *,
                    access_level: Optional[str] = None,
                    is_visible: Optional[bool] = None) -> Any:
        """POST /v1/events/hosts/update — the event creator cannot change."""
        self._check_choice("access_level", access_level, HOST_ACCESS_LEVELS)
        return self._post("/v1/events/hosts/update", self._body(
            {"event_id": self._need(event_id, "event_id"),
             "email": self._need(email, "email")},
            optional={"access_level": access_level, "is_visible": is_visible}))

    def remove_host(self, event_id: str, email: str) -> Any:
        """POST /v1/events/hosts/remove — the event creator cannot be removed."""
        return self._post("/v1/events/hosts/remove", {
            "event_id": self._need(event_id, "event_id"),
            "email": self._need(email, "email")})
