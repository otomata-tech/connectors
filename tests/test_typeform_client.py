"""TypeformClient — verrouille le contrat HTTP construit par le client.

Mocke `requests.Session.request` : verbe + URL + params, sans réseau ni jeton
réel. Cible ce qui pourrait dériver en silence : l'en-tête Bearer, l'hôte du
data center, une méthode par endpoint en lecture, le nettoyage des `None`,
la jonction des paramètres-listes, l'échappement d'un id dans le chemin, et
l'erreur typée d'un refus amont.
"""
import json

import pytest

from oto.tools.common import UpstreamHTTPError
from oto.tools.common.credentials import MissingCredential
from oto.tools.typeform import REGIONS, TypeformClient

US = "https://api.typeform.com"


class _Resp:
    def __init__(self, payload=None, status_code=200):
        self.status_code = status_code
        self._payload = payload
        self.content = b"" if payload is None else json.dumps(payload).encode()
        self.text = self.content.decode()

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _Seen(list):
    """Les appels capturés, plus `responses` : réponses servies dans l'ordre."""


@pytest.fixture
def calls(monkeypatch):
    seen = _Seen()
    responses = []

    def _request(self, method, url, **kwargs):
        seen.append({"method": method, "url": url, "headers": dict(self.headers), **kwargs})
        return responses.pop(0) if responses else _Resp({"items": []})

    monkeypatch.setattr("requests.Session.request", _request)
    seen.responses = responses
    return seen


@pytest.fixture
def client():
    return TypeformClient(access_token="tfp_test")


#: Les lectures d'origine, dont le comportement ne bouge pas avec les écritures
#: (surface complète : `test_typeform_surface_frozen.py`).
READS = {"list_workspaces", "list_forms", "get_form", "list_responses"}


def test_les_lectures_dorigine_restent(client):
    public = {n for n in dir(TypeformClient)
              if not n.startswith("_") and callable(getattr(TypeformClient, n))}
    assert READS <= public


def test_jeton_en_bearer(calls, client):
    client.list_workspaces()
    c = calls[0]
    assert (c["method"], c["url"]) == ("GET", f"{US}/workspaces")
    assert c["headers"]["Authorization"] == "Bearer tfp_test"
    assert c["timeout"] == (10, 60)
    assert c["params"] == {}


def test_jeton_absent_leve_missing_credential():
    with pytest.raises(MissingCredential) as e:
        TypeformClient(access_token=None)
    assert e.value.name == "TYPEFORM_ACCESS_TOKEN"


@pytest.mark.parametrize("region,host", [
    ("us", "https://api.typeform.com"),
    ("eu", "https://api.eu.typeform.com"),
    ("eu2", "https://api.typeform.eu"),
])
def test_la_region_choisit_lhote(calls, region, host):
    TypeformClient(access_token="t", region=region).get_form("abc")
    assert calls[0]["url"] == f"{host}/forms/abc"


def test_region_inconnue_refusee():
    with pytest.raises(ValueError):
        TypeformClient(access_token="t", region="asia")
    assert set(REGIONS) == {"us", "eu", "eu2"}


@pytest.mark.parametrize("call,path", [
    (lambda c: c.list_workspaces(), "/workspaces"),
    (lambda c: c.list_forms(), "/forms"),
    (lambda c: c.get_form("u6nXL7"), "/forms/u6nXL7"),
    (lambda c: c.list_responses("u6nXL7"), "/forms/u6nXL7/responses"),
])
def test_chaque_operation_vise_son_endpoint(calls, client, call, path):
    call(client)
    assert (calls[0]["method"], calls[0]["url"]) == ("GET", f"{US}{path}")


def test_list_forms_params_et_none_ecartes(calls, client):
    client.list_forms(search="NPS", page=2, page_size=50, workspace_id="ws1",
                      sort_by="last_updated_at", order_by="desc", is_public=False)
    assert calls[0]["params"] == {
        "search": "NPS", "page": 2, "page_size": 50, "workspace_id": "ws1",
        "sort_by": "last_updated_at", "order_by": "desc", "is_public": "false"}
    client.list_forms(workspace_id="ws1")
    assert calls[1]["params"] == {"workspace_id": "ws1"}


def test_list_workspaces_params(calls, client):
    client.list_workspaces(search="Ventes", page=1, page_size=200)
    assert calls[0]["params"] == {"search": "Ventes", "page": 1, "page_size": 200}


def test_list_responses_params_listes_jointes(calls, client):
    client.list_responses(
        "f1", page_size=100, since="2026-09-01T00:00:00", until=1767225600,
        before="tok9", included_response_ids=["r1", "r2"],
        response_type=["completed", "partial"], sort="submitted_at,asc",
        query="Lyon", fields=["fA", "fB"], answered_fields=["fA"])
    assert calls[0]["params"] == {
        "page_size": 100, "since": "2026-09-01T00:00:00", "until": 1767225600,
        "before": "tok9", "included_response_ids": "r1,r2",
        "response_type": "completed,partial", "sort": "submitted_at,asc",
        "query": "Lyon", "fields": "fA,fB", "answered_fields": "fA"}


def test_une_chaine_deja_jointe_passe_telle_quelle(calls, client):
    client.list_responses("f1", excluded_response_ids="r1,r2", after="tok1")
    assert calls[0]["params"] == {"excluded_response_ids": "r1,r2", "after": "tok1"}


def test_un_element_avec_virgule_est_refuse(calls, client):
    with pytest.raises(ValueError):
        client.list_responses("f1", fields=["a,b", "c"])
    assert calls == []


def test_lid_est_echappe_dans_le_chemin(calls, client):
    client.get_form("a/b?c")
    assert calls[0]["url"] == f"{US}/forms/a%2Fb%3Fc"


@pytest.mark.parametrize("bad", ["", " ", ".", ".."])
def test_un_id_vide_ou_relatif_est_refuse(calls, client, bad):
    with pytest.raises(ValueError):
        client.list_responses(bad)
    assert calls == []


def test_la_reponse_json_est_rendue_telle_quelle(calls, client):
    payload = {"total_items": 1, "page_count": 1,
               "items": [{"response_id": "r1", "token": "r1", "answers": []}]}
    calls.responses.append(_Resp(payload))
    assert client.list_responses("f1") == payload


def test_erreur_http_typee(calls, client):
    calls.responses.append(_Resp({"code": "AUTHENTICATION_FAILED"}, status_code=401))
    with pytest.raises(UpstreamHTTPError) as exc:
        client.get_form("f1")
    assert exc.value.status_code == 401
    assert exc.value.service == "typeform"
