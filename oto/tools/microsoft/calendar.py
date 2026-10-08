"""Microsoft Graph client — the signed-in person's Outlook calendars (delegated access,
scope `scopes.CALENDAR`).

## Protocol facts that shape a caller

- ⚠️ **Graph mails the attendees by itself.** Creating an event that has
  attendees sends them an invitation, updating it sends them an update, and
  deleting a meeting from the organizer's calendar sends them a cancellation —
  there is no draft step and no way to hold the message back. A caller that
  must not write to third parties checks the attendees before calling.
- **A window is read through `calendarView`**: recurring events come back
  expanded into their occurrences between `start` and `end`, unlike `/events`.
- **Times are returned in the zone asked for** (`Prefer: outlook.timezone`,
  `UTC` by default) as `{"dateTime", "timeZone"}` pairs; a `start` / `end` given
  without an offset is read as UTC by Graph.
- `timezone` takes a Windows (`Romance Standard Time`) or IANA
  (`Europe/Paris`) zone name.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from ..common.credentials import require
from ._transport import GraphBase, segment


def _tz_header(timezone: str) -> Dict[str, str]:
    return {"Prefer": f'outlook.timezone="{require(timezone, "timezone")}"'}


class CalendarClient(GraphBase):
    """The signed-in person's Outlook calendars."""

    @staticmethod
    def _calendar(calendar_id: Optional[str]) -> str:
        """`/me/calendars/{id}`, or `/me/calendar` (the default one) when omitted."""
        if calendar_id:
            return f"/me/calendars/{segment(calendar_id, 'calendar_id')}"
        return "/me/calendar"

    @staticmethod
    def _event(event_id: str) -> str:
        return f"/me/events/{segment(event_id, 'event_id')}"

    def list_calendars(self, *, limit: int = 200) -> List[Dict[str, Any]]:
        """GET /me/calendars — the person's calendars (`id`, `name`, `canEdit`,
        `isDefaultCalendar`, `owner`)."""
        return self._paged("/me/calendars", limit=limit)

    def list_events(self, *, start: str, end: str, calendar_id: Optional[str] = None,
                    limit: int = 50, timezone: str = "UTC") -> List[Dict[str, Any]]:
        """GET …/calendarView — the events (recurrences expanded) between `start`
        and `end` (ISO 8601, e.g. `2026-10-08T00:00:00Z`), earliest first, in the
        default calendar unless `calendar_id`."""
        return self._paged(
            f"{self._calendar(calendar_id)}/calendarView",
            {"startDateTime": require(start, "start"), "endDateTime": require(end, "end"),
             "$orderby": "start/dateTime"},
            limit=limit, headers=_tz_header(timezone))

    def get_event(self, event_id: str, *, timezone: str = "UTC") -> Dict[str, Any]:
        """GET /me/events/{id}."""
        return self._json("GET", self._event(event_id), headers=_tz_header(timezone))

    def create_event(self, *, subject: str, start: str, end: str, timezone: str = "UTC",
                     attendees: Iterable[str] = (), body_html: Optional[str] = None,
                     location: Optional[str] = None, online_meeting: bool = False,
                     calendar_id: Optional[str] = None) -> Dict[str, Any]:
        """POST …/events — a new event in the default calendar unless `calendar_id`.

        ⚠️ With `attendees`, Graph immediately emails each of them an invitation
        from the person's mailbox: this is a message to third parties, not a
        private write.

        Args:
            start / end: local date-times (`2026-10-08T14:00:00`) in `timezone`.
            attendees: email addresses, invited as required attendees.
            online_meeting: `True` adds a Teams meeting link.

        Returns the created event.
        """
        if isinstance(attendees, str):
            raise TypeError("attendees is a list of addresses, not a string")
        event: Dict[str, Any] = {
            "subject": require(subject, "subject"),
            "start": {"dateTime": require(start, "start"),
                      "timeZone": require(timezone, "timezone")},
            "end": {"dateTime": require(end, "end"), "timeZone": timezone},
            "attendees": [{"emailAddress": {"address": require(a, "attendee")},
                           "type": "required"} for a in attendees],
        }
        if body_html is not None:
            event["body"] = {"contentType": "HTML", "content": body_html}
        if location is not None:
            event["location"] = {"displayName": location}
        if online_meeting:
            event["isOnlineMeeting"] = True
            event["onlineMeetingProvider"] = "teamsForBusiness"
        return self._json("POST", f"{self._calendar(calendar_id)}/events", json=event)

    def update_event(self, event_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        """PATCH /me/events/{id} — `patch` in Graph's event shape, sent as is.

        ⚠️ If the event has attendees, Graph emails them the update (and an
        invitation to any attendee added): a message to third parties.

        Returns the updated event."""
        if not patch:
            raise ValueError("patch is empty: name the fields to change")
        return self._json("PATCH", self._event(event_id), json=dict(patch))

    def delete_event(self, event_id: str) -> str:
        """DELETE /me/events/{id}. ⚠️ On the organizer's calendar, Graph emails a
        cancellation to the attendees. Returns the id."""
        self._json("DELETE", self._event(event_id))
        return event_id
