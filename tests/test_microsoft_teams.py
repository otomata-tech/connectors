"""TeamsClient — verrouille le contrat HTTP de Teams : pages plafonnées à 50, pas de
`$top` là où l'amont le refuse, ids encodés, corps des messages postés."""
import pytest

from oto.tools.common import UpstreamHTTPError
from oto.tools.microsoft import TeamsClient
from microsoft_fake import G, _Resp, calls  # noqa: F401

T, C, M = "t1", "19:abc@thread.tacv2", "1700000000000"
CH = f"{G}/teams/t1/channels/19%3Aabc%40thread.tacv2"


@pytest.fixture
def client():
    return TeamsClient("AT-personne")


def test_equipes_et_canaux_sans_top(calls, client):
    calls.responses.extend([_Resp({"value": [{"id": T}]}), _Resp({"value": [{"id": C}]})])
    assert client.list_joined_teams() == [{"id": T}]
    assert client.list_channels(T) == [{"id": C}]
    assert [c["url"] for c in calls] == [f"{G}/me/joinedTeams", f"{G}/teams/t1/channels"]
    assert all("$top" not in c["params"] for c in calls)


def test_messages_d_un_canal_pages_de_50(calls, client):
    calls.responses.extend([
        _Resp({"value": [{"id": str(i)} for i in range(50)], "@odata.nextLink": f"{G}/p2"}),
        _Resp({"value": [{"id": str(i)} for i in range(50, 100)]})])
    assert len(client.list_channel_messages(T, C, limit=60)) == 60
    assert calls[0]["url"] == f"{CH}/messages"
    assert calls[0]["params"] == {"$top": 50}
    assert calls[1]["url"] == f"{G}/p2"


def test_reponses_d_un_fil(calls, client):
    calls.responses.append(_Resp({"value": []}))
    client.list_replies(T, C, M, limit=10)
    assert calls[0]["url"] == f"{CH}/messages/{M}/replies"
    assert calls[0]["params"] == {"$top": 10}


def test_poster_et_repondre_dans_un_canal(calls, client):
    client.post_channel_message(T, C, html="<p>Bonjour</p>")
    client.reply_channel_message(T, C, M, html="<p>Vu</p>")
    assert [(c["method"], c["url"]) for c in calls] == [
        ("POST", f"{CH}/messages"), ("POST", f"{CH}/messages/{M}/replies")]
    assert calls[0]["json"] == {"body": {"contentType": "html", "content": "<p>Bonjour</p>"}}
    assert calls[1]["json"] == {"body": {"contentType": "html", "content": "<p>Vu</p>"}}


def test_conversations(calls, client):
    calls.responses.extend([_Resp({"value": []}), _Resp({"value": []})])
    client.list_chats(limit=200)
    client.list_chat_messages("19:x@unq.gbl.spaces")
    client.post_chat_message("19:x@unq.gbl.spaces", html="<p>Salut</p>")
    assert [c["url"] for c in calls] == [
        f"{G}/me/chats", f"{G}/chats/19%3Ax%40unq.gbl.spaces/messages",
        f"{G}/chats/19%3Ax%40unq.gbl.spaces/messages"]
    assert calls[0]["params"] == {"$top": 50} and calls[1]["params"] == {"$top": 50}
    assert calls[2]["json"] == {"body": {"contentType": "html", "content": "<p>Salut</p>"}}


def test_message_vide_refuse(client):
    with pytest.raises(Exception, match="html"):
        client.post_chat_message("c1", html="")


def test_lecture_d_un_canal_sans_consentement_admin(calls, client):
    calls.responses.append(_Resp({"error": {"code": "Forbidden"}}, status_code=403))
    with pytest.raises(UpstreamHTTPError) as exc:
        client.list_channel_messages(T, C)
    assert exc.value.status_code == 403 and exc.value.service == "microsoft"
