"""CalendarClient — verrouille le contrat HTTP des agendas Outlook : fenêtre par
`calendarView`, fuseau demandé par `Prefer`, corps des écritures."""
import pytest

from oto.tools.common import UpstreamHTTPError
from oto.tools.microsoft import CalendarClient
from microsoft_fake import G, _Resp, calls  # noqa: F401


@pytest.fixture
def client():
    return CalendarClient("AT-personne")


def test_fenetre_d_evenements_en_utc(calls, client):
    calls.responses.append(_Resp({"value": [{"id": "e1"}]}))
    assert client.list_events(start="2026-10-08T00:00:00Z", end="2026-10-09T00:00:00Z",
                              limit=10) == [{"id": "e1"}]
    call = calls[0]
    assert call["url"] == f"{G}/me/calendar/calendarView"
    assert call["params"] == {"startDateTime": "2026-10-08T00:00:00Z",
                              "endDateTime": "2026-10-09T00:00:00Z",
                              "$orderby": "start/dateTime", "$top": 10}
    assert call["headers"]["Prefer"] == 'outlook.timezone="UTC"'


def test_fuseau_suivi_sur_les_pages_suivantes(calls, client):
    calls.responses.extend([_Resp({"value": [{"id": "e1"}], "@odata.nextLink": f"{G}/n"}),
                            _Resp({"value": [{"id": "e2"}]})])
    client.list_events(start="a", end="b", calendar_id="c1", timezone="Europe/Paris")
    assert calls[0]["url"] == f"{G}/me/calendars/c1/calendarView"
    assert calls[1]["headers"]["Prefer"] == 'outlook.timezone="Europe/Paris"'


def test_calendriers_et_lecture(calls, client):
    calls.responses.append(_Resp({"value": []}))
    client.list_calendars()
    client.get_event("e1")
    assert [c["url"] for c in calls] == [f"{G}/me/calendars", f"{G}/me/events/e1"]


def test_creation_d_un_evenement_avec_participants(calls, client):
    client.create_event(subject="Point", start="2026-10-08T14:00:00",
                        end="2026-10-08T15:00:00", timezone="Europe/Paris",
                        attendees=["jane@contoso.com"], body_html="<p>Ordre du jour</p>",
                        location="Salle 2", online_meeting=True, calendar_id="c1")
    call = calls[0]
    assert (call["method"], call["url"]) == ("POST", f"{G}/me/calendars/c1/events")
    assert call["json"] == {
        "subject": "Point",
        "start": {"dateTime": "2026-10-08T14:00:00", "timeZone": "Europe/Paris"},
        "end": {"dateTime": "2026-10-08T15:00:00", "timeZone": "Europe/Paris"},
        "attendees": [{"emailAddress": {"address": "jane@contoso.com"}, "type": "required"}],
        "body": {"contentType": "HTML", "content": "<p>Ordre du jour</p>"},
        "location": {"displayName": "Salle 2"},
        "isOnlineMeeting": True, "onlineMeetingProvider": "teamsForBusiness"}


def test_creation_minimale_sans_participant(calls, client):
    client.create_event(subject="Focus", start="2026-10-08T09:00:00",
                        end="2026-10-08T10:00:00")
    assert calls[0]["url"] == f"{G}/me/calendar/events"
    assert calls[0]["json"]["attendees"] == []
    assert set(calls[0]["json"]) == {"subject", "start", "end", "attendees"}


def test_mise_a_jour_et_suppression(calls, client):
    calls.responses.extend([_Resp({"id": "e1"}), _Resp(status_code=204)])
    assert client.update_event("e1", {"subject": "Nouveau"}) == {"id": "e1"}
    assert client.delete_event("e1") == "e1"
    assert [(c["method"], c["url"]) for c in calls] == [
        ("PATCH", f"{G}/me/events/e1"), ("DELETE", f"{G}/me/events/e1")]
    assert calls[0]["json"] == {"subject": "Nouveau"}
    with pytest.raises(ValueError, match="patch"):
        client.update_event("e1", {})


def test_refus_amont_type(calls, client):
    calls.responses.append(_Resp({"error": {"code": "ErrorItemNotFound"}}, status_code=404))
    with pytest.raises(UpstreamHTTPError) as exc:
        client.delete_event("e1")
    assert exc.value.status_code == 404
