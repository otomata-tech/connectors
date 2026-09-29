"""Google Calendar API client using OAuth2 user credentials."""

from datetime import datetime, timedelta, timezone
from typing import Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from oto.tools.common.credentials import require

SCOPES = ['https://www.googleapis.com/auth/calendar']


class CalendarClientError(Exception):
    """Calendar API error."""


class CalendarClient:
    """Google Calendar API client.

    Args:
        credentials: OAuth2 user credentials, provided by the consumer (required).
    """

    def __init__(self, credentials: Optional[Credentials] = None):
        self.service = build('calendar', 'v3', credentials=require(credentials, 'GOOGLE_CREDENTIALS'))

    def list_calendars(self) -> list[dict]:
        """List all calendars accessible by the user."""
        resp = self.service.calendarList().list().execute()
        return [
            {
                'id': cal['id'],
                'summary': cal.get('summary', ''),
                'primary': cal.get('primary', False),
                'accessRole': cal.get('accessRole', ''),
            }
            for cal in resp.get('items', [])
        ]

    def list_events(
        self,
        calendar_id: str = 'primary',
        time_min: Optional[str] = None,
        time_max: Optional[str] = None,
        max_results: int = 20,
        query: Optional[str] = None,
    ) -> list[dict]:
        """List events from a calendar.

        Args:
            calendar_id: Calendar ID (default: 'primary').
            time_min: Start of time range (ISO 8601). Default: none (no lower bound).
            time_max: End of time range (ISO 8601). Default: none (no upper bound).
            max_results: Maximum number of events.
            query: Free text search query.
        """

        kwargs = {
            'calendarId': calendar_id,
            'maxResults': max_results,
            'singleEvents': True,
            'orderBy': 'startTime',
        }
        if time_min:
            kwargs['timeMin'] = time_min
        if time_max:
            kwargs['timeMax'] = time_max
        if query:
            kwargs['q'] = query

        resp = self.service.events().list(**kwargs).execute()
        return [self._format_event(e) for e in resp.get('items', [])]

    def get_event(self, event_id: str, calendar_id: str = 'primary') -> dict:
        """Get a single event by ID."""
        event = self.service.events().get(
            calendarId=calendar_id, eventId=event_id,
        ).execute()
        return self._format_event(event, detailed=True)

    def today(self, calendar_id: str = 'primary', max_results: int = 50) -> list[dict]:
        """List today's events."""
        now = datetime.now(timezone.utc)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        return self.list_events(
            calendar_id=calendar_id,
            time_min=start.isoformat(),
            time_max=end.isoformat(),
            max_results=max_results,
        )

    def upcoming(
        self,
        days: int = 7,
        calendar_id: str = 'primary',
        max_results: int = 50,
    ) -> list[dict]:
        """List events for the next N days."""
        now = datetime.now(timezone.utc)
        end = now + timedelta(days=days)
        return self.list_events(
            calendar_id=calendar_id,
            time_min=now.isoformat(),
            time_max=end.isoformat(),
            max_results=max_results,
        )

    def create_event(
        self,
        summary: str,
        start: str,
        end: Optional[str] = None,
        description: Optional[str] = None,
        location: Optional[str] = None,
        all_day: bool = False,
        calendar_id: str = 'primary',
        attendees: Optional[list[str]] = None,
        send_updates: str = 'none',
    ) -> dict:
        """Create a calendar event.

        Args:
            summary: Event title.
            start: Start time (ISO 8601 datetime or YYYY-MM-DD for all-day).
            end: End time. If None, defaults to start + 1 hour (or +1 day for all-day).
            description: Event description.
            location: Event location.
            all_day: If True, treat start/end as dates (YYYY-MM-DD).
            calendar_id: Calendar ID.
            attendees: guest email addresses to invite (signal #862 — the event
                could be created, nobody could be invited to it).
            send_updates: 'none' (default) | 'all' | 'externalOnly' — passed
                EXPLICITLY, as in `update_event`: inviting must not mail guests
                unless asked.
        """
        if all_day or len(start) == 10:  # YYYY-MM-DD
            body: dict = {
                'summary': summary,
                'start': {'date': start},
                'end': {'date': end or start},
            }
        else:
            body = {
                'summary': summary,
                'start': {'dateTime': start},
                'end': {'dateTime': end or (datetime.fromisoformat(start) + timedelta(hours=1)).isoformat()},
            }
        if description:
            body['description'] = description
        if location:
            body['location'] = location
        if attendees:
            body['attendees'] = [{'email': e} for e in attendees]
        event = self.service.events().insert(
            calendarId=calendar_id, body=body, sendUpdates=send_updates).execute()
        return self._format_event(event)

    def update_event(
        self,
        event_id: str,
        summary: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        description: Optional[str] = None,
        location: Optional[str] = None,
        all_day: bool = False,
        calendar_id: str = 'primary',
        send_updates: str = 'none',
        attendees: Optional[list[str]] = None,
    ) -> dict:
        """Patch an existing event — only the fields you pass are touched.

        `attendees` REPLACES the whole guest list when given (Google: « array fields,
        if specified, overwrite the existing arrays ») — pass the full list, current
        guests included ; `[]` removes every guest ; None leaves them alone.

        Uses `events.patch`, NOT `events.update`: the latter REPLACES the whole
        event, so any field left out (attendees, recurrence, reminders, conference
        data) would be silently dropped. A caller fixing a typo in the title would
        cancel the meeting room.

        ⚠️ `send_updates` is passed EXPLICITLY (default 'none') rather than relying
        on the API default: correcting a title should not mail every attendee, and a
        default we do not control could change under us. Pass 'all' to notify.

        Args:
            event_id: the event to patch (from list/get).
            summary/start/end/description/location: only what you pass is changed.
            all_day: treat start/end as dates (YYYY-MM-DD) rather than datetimes.
            send_updates: 'none' (default) | 'all' | 'externalOnly'.
        """
        body: dict = {}
        if summary is not None:
            body['summary'] = summary
        if description is not None:
            body['description'] = description
        if location is not None:
            body['location'] = location
        if start is not None:
            body['start'] = {'date': start} if (all_day or len(start) == 10) \
                else {'dateTime': start}
        if end is not None:
            body['end'] = {'date': end} if (all_day or len(end) == 10) \
                else {'dateTime': end}
        if attendees is not None:
            body['attendees'] = [{'email': e} for e in attendees]
        if not body:
            raise ValueError(
                "update_event: nothing to change — pass at least one of summary, "
                "start, end, description, location, attendees. An empty patch would "
                "spend a write and report success without touching anything.")
        event = self.service.events().patch(
            calendarId=calendar_id, eventId=event_id, body=body,
            sendUpdates=send_updates).execute()
        return self._format_event(event)

    def delete_event(self, event_id: str, calendar_id: str = 'primary',
                     send_updates: str = 'none') -> dict:
        """Delete an event. Returns what was deleted, never an empty success.

        `events.delete` answers 204 with NO body, so there is nothing to echo back
        from the API: the event is read FIRST and its summary/start returned with the
        confirmation. Without that, a caller deleting the wrong id would get the same
        answer as one deleting the right one.

        ⚠️ Irreversible, and `send_updates` defaults to 'none' for the same reason as
        `update_event` — cancelling a meeting mails its attendees.
        """
        avant = self.get_event(event_id, calendar_id=calendar_id)
        self.service.events().delete(
            calendarId=calendar_id, eventId=event_id,
            sendUpdates=send_updates).execute()
        return {'deleted': True, 'id': event_id,
                'summary': avant.get('summary'), 'start': avant.get('start'),
                'calendar_id': calendar_id, 'notified': send_updates != 'none'}

    @staticmethod
    def _format_event(event: dict, detailed: bool = False) -> dict:
        """Format an event into a clean dict."""
        start = event.get('start', {})
        end = event.get('end', {})
        result = {
            'id': event['id'],
            'summary': event.get('summary', '(no title)'),
            'start': start.get('dateTime', start.get('date', '')),
            'end': end.get('dateTime', end.get('date', '')),
            'status': event.get('status', ''),
            'transparency': event.get('transparency', 'opaque'),
            'htmlLink': event.get('htmlLink', ''),
        }
        location = event.get('location')
        if location:
            result['location'] = location

        hangout = event.get('hangoutLink')
        if hangout:
            result['hangoutLink'] = hangout

        organizer = event.get('organizer', {})
        if organizer:
            result['organizer'] = organizer.get('email', '')

        if detailed:
            result['description'] = event.get('description', '')
            attendees = event.get('attendees', [])
            if attendees:
                result['attendees'] = [
                    {
                        'email': a.get('email', ''),
                        'responseStatus': a.get('responseStatus', ''),
                        'displayName': a.get('displayName', ''),
                    }
                    for a in attendees
                ]
            result['recurrence'] = event.get('recurrence', [])
            reminders = event.get('reminders', {})
            if reminders:
                result['reminders'] = reminders

        return result
