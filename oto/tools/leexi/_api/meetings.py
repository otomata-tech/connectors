"""Leexi meeting events — and the assistant sent to them.

This mixin is never instantiated on its own: it is composed into `LeexiClient`, which
provides the transport (`_request`, `_list`, `_check_choice`).

A « meeting event » is a meeting KNOWN to Leexi (coming from the calendar, from a
manual entry, or from this API) — distinct from a « call », which is an
already-processed recording. The assistant is launched on the former and produces the latter.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..const import (MEETING_DATE_FILTERS, MEETING_ORDERS, MEETING_ORIGINS)


class _MeetingsMixin:
    """Meeting events and assistant."""

    def list_meeting_events(self, page: Optional[int] = None,
                            items: Optional[int] = None,
                            order: Optional[str] = None,
                            origin: Optional[str] = None,
                            date_filter: Optional[str] = None,
                            date_from: Optional[str] = None,
                            date_to: Optional[str] = None) -> Any:
        """GET /v1/meeting_events — known meetings. Scope `read_meeting_events`.

        `origin` distinguishes what comes from the calendar, from a manual entry or from
        the API (`calendar` / `manual` / `api`). `date_from`/`date_to` bound the
        field named by `date_filter` (default `start_time`) — prefixed here because
        `from` is a reserved word in Python, sent as `from`/`to` on the wire.
        """
        self._check_choice("order", order, MEETING_ORDERS)
        self._check_choice("origin", origin, MEETING_ORIGINS)
        self._check_choice("date_filter", date_filter, MEETING_DATE_FILTERS)
        return self._list("/meeting_events", page, items, {
            "order": order, "origin": origin, "date_filter": date_filter,
            "from": date_from, "to": date_to,
        })

    def get_meeting_event(self, uuid: str) -> Any:
        """GET /v1/meeting_events/{uuid} — one meeting. Scope `read_meeting_events`."""
        return self._request("GET", f"/meeting_events/{uuid}")

    def create_meeting_event(self, payload: Dict[str, Any]) -> Any:
        """POST /v1/meeting_events — declares a meeting. Scope `write_meeting_events`.

        Required: `end_time`, `internal`, `meeting_url`, `organizer`, `owned`,
        `start_time`, `to_record`, `user_uuid`. Optional: `attendees`,
        `description`, `direction` (`inbound`/`outbound`), `title`.

        `to_record=True` requests that the meeting be recorded. A meeting already
        declared (same URL, same slot) returns **409**.
        """
        return self._request("POST", "/meeting_events", json=dict(payload))

    def delete_meeting_event(self, uuid: str) -> Any:
        """DELETE /v1/meeting_events/{uuid} — removes a meeting. Scope `write_meeting_events`."""
        return self._request("DELETE", f"/meeting_events/{uuid}")

    def launch_meeting_assistant(self, uuid: str,
                                 stop_task: Optional[bool] = None) -> Any:
        """POST /v1/meeting_events/{uuid}/launch_bot — sends (or removes) the assistant.

        Scope `write_meeting_events`.

        ⚠️ **A single endpoint for both directions**: `stop_task=True` STOPS a running
        bot instead of launching one. The upstream name (`launch_bot`) does not say so,
        hence this explicit parameter rather than two methods that would lie.

        A bot already launched returns **409**; **405** signals an action that is impossible for
        this event (past meeting, no usable URL…).
        """
        body: Dict[str, Any] = {}
        if stop_task is not None:
            body["stop_task"] = stop_task
        return self._request("POST", f"/meeting_events/{uuid}/launch_bot",
                             json=body or None)
