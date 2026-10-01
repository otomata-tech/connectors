"""GraphClient + auth Microsoft — verrouille le contrat HTTP construit par le client.

Mocke `requests.post` (jeton) et `requests.Session.request` (Graph), sans réseau ni
credential réel. Cible ce qui pourrait dériver en silence : le secret dans le corps
et jamais dans l'URL, le cache de jeton process-wide keyé par credential, le
rejeu unique sur 401, l'adressage d'un item par id ou par chemin, la pagination
`@odata.nextLink` bornée, et la cible d'un upload.
"""
import json

import pytest

from oto.tools.common import UpstreamHTTPError
from oto.tools.common.credentials import MissingCredential
from oto.tools.microsoft import GraphClient, MicrosoftAuthError
from oto.tools.microsoft import auth as ms_auth

G = "https://graph.microsoft.com/v1.0"
TENANT, CID, SECRET = "tenant-guid", "client-guid", "s3cr3t-value"


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


@pytest.fixture(autouse=True)
def _clear_cache():
    ms_auth._TOKEN_CACHE.clear()
    yield
    ms_auth._TOKEN_CACHE.clear()


@pytest.fixture
def token_calls(monkeypatch):
    seen = []

    def fake_post(url, **kw):
        seen.append({"url": url, **kw})
        return _Resp({"access_token": f"AT{len(seen)}", "expires_in": 3600})

    monkeypatch.setattr(ms_auth.requests, "post", fake_post)
    return seen


@pytest.fixture
def calls(monkeypatch, token_calls):
    seen = _Seen()
    seen.responses = responses = []

    def _request(self, method, url, **kwargs):
        seen.append({"method": method, "url": url, **kwargs})
        return responses.pop(0) if responses else _Resp({"ok": True})

    monkeypatch.setattr("requests.Session.request", _request)
    return seen


@pytest.fixture
def client():
    return GraphClient(TENANT, CID, SECRET)


# --- construction & auth ------------------------------------------------------

@pytest.mark.parametrize("args, nom", [
    ((None, CID, SECRET), "MICROSOFT_TENANT_ID"),
    ((TENANT, None, SECRET), "MICROSOFT_CLIENT_ID"),
    ((TENANT, CID, ""), "MICROSOFT_CLIENT_SECRET"),
])
def test_credential_manquant_nomme(args, nom):
    with pytest.raises(MissingCredential) as exc:
        GraphClient(*args)
    assert exc.value.name == nom


def test_secret_dans_le_corps_jamais_dans_l_url(token_calls):
    assert ms_auth.get_access_token(TENANT, CID, SECRET) == "AT1"
    call = token_calls[0]
    assert call["url"] == f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token"
    assert SECRET not in call["url"] and "params" not in call
    assert call["data"] == {"grant_type": "client_credentials", "client_id": CID,
                            "client_secret": SECRET,
                            "scope": "https://graph.microsoft.com/.default"}


def test_cache_process_wide_keye_par_credential(token_calls):
    ms_auth.get_access_token(TENANT, CID, SECRET)
    ms_auth.get_access_token(TENANT, CID, SECRET)
    assert len(token_calls) == 1
    ms_auth.get_access_token(TENANT, CID, "autre-secret")
    assert len(token_calls) == 2
    assert all(SECRET not in k for k in ms_auth._TOKEN_CACHE)


def test_refus_entra_nomme_sans_secret(monkeypatch):
    desc = ("AADSTS7000215: Invalid client secret provided.\r\n"
            "Trace ID: x Correlation ID: y Timestamp: z")
    monkeypatch.setattr(ms_auth.requests, "post", lambda url, **kw: _Resp(
        {"error": "invalid_client", "error_description": desc}, status_code=401))
    with pytest.raises(MicrosoftAuthError) as exc:
        ms_auth.get_access_token(TENANT, CID, SECRET)
    assert exc.value.status_code == 401
    assert exc.value.code == "AADSTS7000215"
    assert "Invalid client secret" in str(exc.value)
    assert "Trace ID" not in str(exc.value) and SECRET not in str(exc.value)


def test_bearer_et_rejeu_unique_sur_401(calls, client, token_calls):
    calls.responses.extend([_Resp({"error": {}}, status_code=401), _Resp({"id": "s1"})])
    assert client.get_site("s1") == {"id": "s1"}
    assert [c["headers"]["Authorization"] for c in calls] == ["Bearer AT1", "Bearer AT2"]
    assert len(token_calls) == 2


def test_refus_amont_type(calls, client):
    calls.responses.append(_Resp({"error": {"code": "accessDenied"}}, status_code=403))
    with pytest.raises(UpstreamHTTPError) as exc:
        client.get_site("s1")
    assert exc.value.status_code == 403 and exc.value.service == "microsoft"


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
    with pytest.raises(ValueError, match="s'excluent"):
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
