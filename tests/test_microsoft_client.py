"""GraphClient + auth Microsoft — verrouille le contrat HTTP construit par la lib.

Mocke `requests.post` (serveur d'autorisation) et `requests.Session.request`
(Graph), sans réseau ni credential réel. Cible ce qui pourrait dériver en silence :
les secrets dans le corps et jamais dans l'URL, la rotation du refresh token, le
classement « autorisation morte » contre « configuration fausse », le jeton porté
en Bearer, l'adressage d'un item par id ou par chemin, la pagination
`@odata.nextLink` bornée, et la cible d'un upload.
"""
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from oto.tools.common import UpstreamHTTPError
from oto.tools.common.credentials import MissingCredential
from oto.tools.microsoft import (FILES_SCOPES, GraphClient, MicrosoftAuthError,
                                 MicrosoftGrantExpired)
from oto.tools.microsoft import auth as ms_auth

G = "https://graph.microsoft.com/v1.0"
TOKEN_URL = "https://login.microsoftonline.com/organizations/oauth2/v2.0/token"
CID, SECRET, REDIRECT = "client-guid", "s3cr3t-value", "https://oto.example/cb"


class _Resp:
    def __init__(self, payload=None, status_code=200, raw=None):
        self.status_code = status_code
        self._payload = payload
        if raw is not None:
            self.content = raw
            self.text = raw.decode(errors="replace")
        else:
            self.content = b"" if payload is None else json.dumps(payload).encode()
            self.text = self.content.decode()

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _Seen(list):
    """The captured calls, plus `responses`: queued replies, served in order."""


@pytest.fixture
def token_calls(monkeypatch):
    seen = _Seen()
    seen.responses = []

    def fake_post(url, **kw):
        seen.append({"url": url, **kw})
        return seen.responses.pop(0) if seen.responses else _Resp(
            {"access_token": "AT", "refresh_token": "RT2", "expires_in": 3599,
             "scope": "Files.ReadWrite.All"})

    monkeypatch.setattr(ms_auth.requests, "post", fake_post)
    return seen


@pytest.fixture
def calls(monkeypatch):
    seen = _Seen()
    seen.responses = responses = []

    def _request(self, method, url, **kwargs):
        seen.append({"method": method, "url": url, "headers": dict(self.headers), **kwargs})
        return responses.pop(0) if responses else _Resp({"ok": True})

    monkeypatch.setattr("requests.Session.request", _request)
    return seen


@pytest.fixture
def client():
    return GraphClient("AT-personne")


# --- auth : connexion d'une personne --------------------------------------------

def test_url_d_autorisation():
    url = ms_auth.authorize_url(CID, REDIRECT, "etat-signe")
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == (
        "https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize")
    q = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert q == {"client_id": CID, "response_type": "code", "redirect_uri": REDIRECT,
                 "response_mode": "query", "scope": " ".join(FILES_SCOPES),
                 "state": "etat-signe", "prompt": "select_account"}
    assert "offline_access" in FILES_SCOPES


def test_echange_du_code_secret_dans_le_corps(token_calls):
    grant = ms_auth.exchange_code(CID, SECRET, "le-code", REDIRECT)
    call = token_calls[0]
    assert call["url"] == TOKEN_URL and "params" not in call
    assert SECRET not in call["url"]
    assert call["data"] == {"grant_type": "authorization_code", "client_id": CID,
                            "client_secret": SECRET, "code": "le-code",
                            "redirect_uri": REDIRECT, "scope": " ".join(FILES_SCOPES)}
    assert (grant.access_token, grant.refresh_token, grant.expires_in) == ("AT", "RT2", 3599)


def test_renouvellement_rend_le_refresh_token_tourne(token_calls):
    grant = ms_auth.refresh(CID, SECRET, "RT1")
    assert token_calls[0]["data"]["grant_type"] == "refresh_token"
    assert token_calls[0]["data"]["refresh_token"] == "RT1"
    assert grant.refresh_token == "RT2"


def test_renouvellement_sans_rotation_garde_l_ancien(token_calls):
    token_calls.responses.append(_Resp({"access_token": "AT", "expires_in": 60}))
    assert ms_auth.refresh(CID, SECRET, "RT1").refresh_token == "RT1"


def test_autorisation_morte_classee_a_part(token_calls):
    token_calls.responses.append(_Resp({
        "error": "invalid_grant",
        "error_description": "AADSTS70008: The refresh token has expired.\r\nTrace ID: x"},
        status_code=400))
    with pytest.raises(MicrosoftGrantExpired) as exc:
        ms_auth.refresh(CID, SECRET, "RT1")
    assert exc.value.code == "AADSTS70008" and "Trace ID" not in str(exc.value)


def test_secret_d_application_faux_n_est_pas_une_autorisation_morte(token_calls):
    token_calls.responses.append(_Resp({
        "error": "invalid_client",
        "error_description": "AADSTS7000215: Invalid client secret provided."},
        status_code=401))
    with pytest.raises(MicrosoftAuthError) as exc:
        ms_auth.refresh(CID, SECRET, "RT1")
    assert not isinstance(exc.value, MicrosoftGrantExpired)
    assert exc.value.code == "AADSTS7000215" and SECRET not in str(exc.value)


@pytest.mark.parametrize("fn, nom", [
    (lambda: ms_auth.refresh(CID, "", "RT"), "MICROSOFT_CLIENT_SECRET"),
    (lambda: ms_auth.refresh(CID, SECRET, ""), "MICROSOFT_REFRESH_TOKEN"),
    (lambda: GraphClient(None), "MICROSOFT_ACCESS_TOKEN"),
])
def test_credential_manquant_nomme(fn, nom):
    with pytest.raises(MissingCredential) as exc:
        fn()
    assert exc.value.name == nom


# --- client : le jeton de la personne --------------------------------------------

def test_bearer_de_la_personne(calls, client):
    client.get_me()
    client.get_my_drive()
    assert [c["url"] for c in calls] == [f"{G}/me", f"{G}/me/drive"]
    assert calls[0]["headers"]["Authorization"] == "Bearer AT-personne"


def test_refus_amont_type(calls, client):
    calls.responses.append(_Resp({"error": {"code": "InvalidAuthenticationToken"}},
                                 status_code=401))
    with pytest.raises(UpstreamHTTPError) as exc:
        client.get_site("s1")
    assert exc.value.status_code == 401 and exc.value.service == "microsoft"
    assert len(calls) == 1


# --- sites & drives ------------------------------------------------------------

def test_search_sites_pagine_et_borne(calls, client):
    calls.responses.extend([
        _Resp({"value": [{"id": "a"}, {"id": "b"}], "@odata.nextLink": f"{G}/sites?p=2"}),
        _Resp({"value": [{"id": "c"}, {"id": "d"}], "@odata.nextLink": f"{G}/sites?p=3"}),
    ])
    assert [s["id"] for s in client.search_sites("marketing", limit=3)] == ["a", "b", "c"]
    assert calls[0]["url"] == f"{G}/sites"
    assert calls[0]["params"] == {"search": "marketing", "$top": 3}
    assert calls[1]["url"] == f"{G}/sites?p=2"
    assert len(calls) == 2


def test_site_par_chemin(calls, client):
    client.get_site_by_path("contoso.sharepoint.com", "/sites/Équipe RH/")
    assert calls[0]["url"] == f"{G}/sites/contoso.sharepoint.com:/sites/%C3%89quipe%20RH"


def test_onedrive_d_un_utilisateur(calls, client):
    client.get_user_drive("jane@contoso.com")
    assert calls[0]["url"] == f"{G}/users/jane@contoso.com/drive"


# --- items ---------------------------------------------------------------------

def test_item_par_id_par_chemin_ou_racine(calls, client):
    client.get_item("d1", item_id="i1")
    client.get_item("d1", path="/Contrats/2026/nda v2.docx")
    client.get_item("d1")
    assert [c["url"] for c in calls] == [
        f"{G}/drives/d1/items/i1",
        f"{G}/drives/d1/root:/Contrats/2026/nda%20v2.docx:",
        f"{G}/drives/d1/root",
    ]


def test_item_id_et_path_s_excluent(client):
    with pytest.raises(ValueError, match="mutually exclusive"):
        client.get_item("d1", item_id="i1", path="a")


def test_children_d_un_dossier_par_chemin(calls, client):
    calls.responses.append(_Resp({"value": [{"id": "x"}]}))
    assert client.list_children("d1", path="Contrats") == [{"id": "x"}]
    assert calls[0]["url"] == f"{G}/drives/d1/root:/Contrats:/children"


def test_search_items_echappe_l_apostrophe(calls, client):
    calls.responses.append(_Resp({"value": []}))
    client.search_items("d1", "l'offre")
    assert calls[0]["url"] == f"{G}/drives/d1/root/search(q='l%27%27offre')"


def test_download_rend_les_octets_et_convertit(calls, client):
    calls.responses.append(_Resp(raw=b"%PDF-1.7"))
    assert client.download("d1", item_id="i1", format="pdf") == b"%PDF-1.7"
    assert calls[0]["url"] == f"{G}/drives/d1/items/i1/content"
    assert calls[0]["params"] == {"format": "pdf"}


@pytest.mark.parametrize("kw, cible", [
    ({}, f"{G}/drives/d1/root:/nda.pdf:/content"),
    ({"parent_id": "f1"}, f"{G}/drives/d1/items/f1:/nda.pdf:/content"),
    ({"parent_path": "Contrats/2026"}, f"{G}/drives/d1/root:/Contrats/2026/nda.pdf:/content"),
])
def test_upload_cible(calls, client, kw, cible):
    client.upload("d1", "nda.pdf", b"%PDF", content_type="application/pdf", **kw)
    call = calls[0]
    assert call["method"] == "PUT" and call["url"] == cible
    assert call["data"] == b"%PDF"
    assert call["params"] == {"@microsoft.graph.conflictBehavior": "fail"}
    assert call["headers"]["Content-Type"] == "application/pdf"


def test_upload_refuse_un_chemin_comme_nom(client):
    with pytest.raises(ValueError, match="filename"):
        client.upload("d1", "a/b.pdf", b"x")


def test_create_folder(calls, client):
    client.create_folder("d1", "2027", parent_path="Contrats")
    call = calls[0]
    assert call["method"] == "POST"
    assert call["url"] == f"{G}/drives/d1/root:/Contrats:/children"
    assert call["json"] == {"name": "2027", "folder": {},
                            "@microsoft.graph.conflictBehavior": "fail"}


def test_conflit_inconnu_refuse(client):
    with pytest.raises(ValueError, match="conflict"):
        client.create_folder("d1", "x", conflict="overwrite")
