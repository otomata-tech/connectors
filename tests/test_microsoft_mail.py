"""MailClient — verrouille le contrat HTTP de la boîte Outlook : recherche KQL contre
liste triée, corps demandé par `Prefer`, brouillons (nouveau, réponse au-dessus de la
citation), envoi séparé, pièces jointes bornées à 3 Mo, erreurs amont typées."""
import base64

import pytest

from oto.tools.common import UpstreamHTTPError
from oto.tools.microsoft import MailClient
from oto.tools.microsoft.mail import MAX_INLINE_ATTACHMENT
from microsoft_fake import G, _Resp, calls  # noqa: F401


@pytest.fixture
def client():
    return MailClient("AT-personne")


def test_liste_recente_d_un_dossier_bien_connu(calls, client):
    calls.responses.append(_Resp({"value": [{"id": "m1"}]}))
    assert client.search_messages(folder="inbox", top=5) == [{"id": "m1"}]
    call = calls[0]
    assert call["url"] == f"{G}/me/mailFolders/inbox/messages"
    assert call["params"]["$orderby"] == "receivedDateTime desc"
    assert call["params"]["$top"] == 5 and "$search" not in call["params"]
    assert call["headers"]["Authorization"] == "Bearer AT-personne"


def test_non_lus_filtre_compatible_avec_le_tri(calls, client):
    calls.responses.append(_Resp({"value": []}))
    client.search_messages(unread=True)
    params = calls[0]["params"]
    assert calls[0]["url"] == f"{G}/me/messages"
    assert params["$filter"] == "receivedDateTime ge 1900-01-01T00:00:00Z and isRead eq false"


def test_recherche_kql_sans_tri(calls, client):
    calls.responses.append(_Resp({"value": []}))
    client.search_messages('from:jane "q3 report"')
    params = calls[0]["params"]
    assert params["$search"] == '"from:jane \\"q3 report\\""'
    assert "$orderby" not in params and "$filter" not in params


def test_recherche_et_non_lus_refuses_ensemble(client):
    with pytest.raises(ValueError, match="unread"):
        client.search_messages("invoice", unread=True)


def test_corps_demande_en_texte_ou_html(calls, client):
    client.get_message("AAMk=")
    client.get_message("AAMk=", body="html")
    assert calls[0]["url"] == f"{G}/me/messages/AAMk%3D"
    assert calls[0]["headers"]["Prefer"] == 'outlook.body-content-type="text"'
    assert calls[1]["headers"]["Prefer"] == 'outlook.body-content-type="html"'
    with pytest.raises(ValueError, match="body"):
        client.get_message("m", body="rtf")


def test_pieces_jointes(calls, client):
    calls.responses.append(_Resp({"value": [{"id": "a1"}]}))
    assert client.list_attachments("m1") == [{"id": "a1"}]
    client.get_attachment("m1", "a1")
    assert calls[0]["url"] == f"{G}/me/messages/m1/attachments"
    assert "$top" not in calls[0]["params"] and "contentBytes" not in calls[0]["params"]["$select"]
    assert calls[1]["url"] == f"{G}/me/messages/m1/attachments/a1"


def test_brouillon_avec_piece_jointe(calls, client):
    calls.responses.append(_Resp({"id": "d1", "isDraft": True}))
    draft = client.create_draft(
        to=["jane@contoso.com"], cc=["bob@contoso.com"], subject="Devis",
        body_html="<p>Bonjour</p>",
        attachments=[{"name": "devis.pdf", "content_type": "application/pdf",
                      "content_bytes": b"%PDF"}])
    assert draft["id"] == "d1"
    create, attach = calls
    assert (create["method"], create["url"]) == ("POST", f"{G}/me/messages")
    assert create["json"] == {
        "subject": "Devis", "body": {"contentType": "HTML", "content": "<p>Bonjour</p>"},
        "toRecipients": [{"emailAddress": {"address": "jane@contoso.com"}}],
        "ccRecipients": [{"emailAddress": {"address": "bob@contoso.com"}}],
        "bccRecipients": []}
    assert (attach["method"], attach["url"]) == ("POST", f"{G}/me/messages/d1/attachments")
    assert attach["json"] == {"@odata.type": "#microsoft.graph.fileAttachment",
                              "name": "devis.pdf", "contentType": "application/pdf",
                              "contentBytes": base64.b64encode(b"%PDF").decode()}


def test_piece_jointe_de_plus_de_3_mo_refusee_avant_toute_ecriture(calls, client):
    with pytest.raises(ValueError, match="at most"):
        client.create_draft(to=["a@b.c"], subject="s", body_html="", attachments=[
            {"name": "gros.zip", "content_bytes": b"0" * (MAX_INLINE_ATTACHMENT + 1)}])
    assert calls == []


def test_destinataires_en_chaine_refuses(client):
    with pytest.raises(TypeError, match="list"):
        client.create_draft(to="jane@contoso.com", subject="s", body_html="")


def test_brouillon_de_reponse_au_dessus_de_la_citation(calls, client):
    calls.responses.extend([
        _Resp({"id": "r1", "body": {"contentType": "html",
                                    "content": "<html><body dir=ltr><hr>cité</body></html>"}}),
        _Resp({"id": "r1"}),
    ])
    assert client.create_reply_draft("m1", body_html="<p>OK</p>", reply_all=True) == {"id": "r1"}
    create, patch = calls
    assert (create["method"], create["url"]) == ("POST", f"{G}/me/messages/m1/createReplyAll")
    assert (patch["method"], patch["url"]) == ("PATCH", f"{G}/me/messages/r1")
    assert patch["json"] == {"body": {"contentType": "HTML", "content":
                                      "<html><body dir=ltr><p>OK</p><hr>cité</body></html>"}}


def test_reponse_a_un_message_texte_echappe_la_citation(calls, client):
    calls.responses.extend([_Resp({"id": "r1", "body": {"contentType": "text",
                                                        "content": "a < b"}}), _Resp({})])
    client.create_reply_draft("m1", body_html="<p>OK</p>")
    assert calls[0]["url"] == f"{G}/me/messages/m1/createReply"
    assert calls[1]["json"]["body"]["content"] == (
        '<p>OK</p><div style="white-space:pre-wrap">a &lt; b</div>')


def test_envoi_deplacement_suppression(calls, client):
    calls.responses.extend([_Resp(status_code=202), _Resp({"id": "m1-new"}),
                            _Resp(status_code=204)])
    assert client.send_draft("d1") == "d1"
    assert client.move("m1", "archive") == {"id": "m1-new"}
    assert client.delete("m1") == "m1"
    assert [(c["method"], c["url"]) for c in calls] == [
        ("POST", f"{G}/me/messages/d1/send"),
        ("POST", f"{G}/me/messages/m1/move"),
        ("DELETE", f"{G}/me/messages/m1")]
    assert calls[1]["json"] == {"destinationId": "archive"}


def test_dossiers(calls, client):
    calls.responses.append(_Resp({"value": [{"id": "f1", "displayName": "Inbox"}]}))
    assert client.list_folders()[0]["id"] == "f1"
    assert calls[0]["url"] == f"{G}/me/mailFolders"


def test_refus_amont_type(calls, client):
    calls.responses.append(_Resp({"error": {"code": "ErrorAccessDenied"}}, status_code=403))
    with pytest.raises(UpstreamHTTPError) as exc:
        client.send_draft("d1")
    assert exc.value.status_code == 403 and exc.value.service == "microsoft"
