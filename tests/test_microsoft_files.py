"""FilesClient + transport Graph — verrouille le contrat HTTP construit par la lib.

Cible ce qui pourrait dériver en silence : le jeton porté en Bearer, l'adressage
d'un item par id ou par chemin, la pagination `@odata.nextLink` bornée, la cible
d'un upload, et le contrat d'erreur amont.
"""
import pytest

from oto.tools.common import UpstreamHTTPError
from oto.tools.common.credentials import MissingCredential
from oto.tools.microsoft import FilesClient
from microsoft_fake import G, _Resp, calls  # noqa: F401


@pytest.fixture
def client():
    return FilesClient("AT-personne")


def test_jeton_manquant_nomme():
    with pytest.raises(MissingCredential) as exc:
        FilesClient(None)
    assert exc.value.name == "MICROSOFT_ACCESS_TOKEN"


def test_pagination_s_arrete_a_la_limite_sans_page_de_trop(calls, client):
    calls.responses.extend([
        _Resp({"value": [{"id": "a"}], "@odata.nextLink": f"{G}/x?p=2"}),
        _Resp({"value": [{"id": "b"}]}),
    ])
    assert [i["id"] for i in client._paged("/x", {"q": 1}, limit=10)] == ["a", "b"]
    assert calls[0]["params"] == {"q": 1, "$top": 10}
    assert len(calls) == 2


def test_pagination_sans_top_quand_l_amont_le_refuse(calls, client):
    calls.responses.append(_Resp({"value": [{"id": "a"}, {"id": "b"}]}))
    assert client._paged("/x", limit=1, page_size=None) == [{"id": "a"}]
    assert calls[0]["params"] == {}


def test_pagination_plafonne_la_page(calls, client):
    calls.responses.append(_Resp({"value": []}))
    client._paged("/x", limit=500, page_size=50)
    assert calls[0]["params"] == {"$top": 50}


def test_limite_nulle_refusee(client):
    with pytest.raises(ValueError, match="limit"):
        client._paged("/x", limit=0)


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
