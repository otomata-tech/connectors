"""TypeformClient — écritures : formulaires, webhooks, suppression de réponses.

Mocke `requests.Session.request`, sans réseau ni jeton réel. Cible : verbe,
URL et corps JSON de chaque écriture ; aucun corps envoyé quand il n'y en a pas ;
rien d'envoyé quand l'entrée est refusée localement ; 204 rendu `None` ; le
secret de signature d'un webhook jamais rendu.
"""
import json

import pytest

from oto.tools.common import UpstreamHTTPError
from oto.tools.typeform import TypeformClient
from oto.tools.typeform._api.forms import PATCH_PATHS

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
    """Les appels capturés, plus `responses` : réponses servies dans l'ordre
    (204 vide une fois la file épuisée)."""


@pytest.fixture
def calls(monkeypatch):
    seen = _Seen()
    seen.responses = []

    def _request(self, method, url, **kwargs):
        seen.append({"method": method, "url": url, **kwargs})
        return seen.responses.pop(0) if seen.responses else _Resp(None, 204)

    monkeypatch.setattr("requests.Session.request", _request)
    return seen


@pytest.fixture
def client():
    return TypeformClient(access_token="tfp_test")


# --- Formulaires -----------------------------------------------------------


def test_create_form_poste_le_corps_sans_none(calls, client):
    calls.responses.append(_Resp({"id": "new1", "title": "T"}, 201))
    out = client.create_form(title="T", settings={"is_public": False},
                             fields=[{"title": "Q", "type": "short_text"}])
    c = calls[0]
    assert (c["method"], c["url"]) == ("POST", f"{US}/forms")
    assert c["json"] == {"title": "T", "settings": {"is_public": False},
                         "fields": [{"title": "Q", "type": "short_text"}]}
    assert "params" not in c and c["timeout"] == (10, 60)
    assert out == {"id": "new1", "title": "T"}


def test_create_form_type_passe_sous_son_nom(calls, client):
    calls.responses.append(_Resp({"id": "x"}, 201))
    client.create_form(title="Quiz", type="score")
    assert calls[0]["json"] == {"title": "Quiz", "type": "score"}


@pytest.mark.parametrize("title", ["", "  ", None])
def test_un_titre_vide_est_refuse_avant_lappel(calls, client, title):
    with pytest.raises(ValueError):
        client.create_form(title=title)
    assert calls == []


def test_replace_form_met_tout_le_formulaire(calls, client):
    calls.responses.append(_Resp({"id": "f1"}))
    client.replace_form("f1", title="T2", fields=[{"id": "a", "title": "Q", "type": "nps"}])
    c = calls[0]
    assert (c["method"], c["url"]) == ("PUT", f"{US}/forms/f1")
    assert c["json"] == {"title": "T2", "fields": [{"id": "a", "title": "Q", "type": "nps"}]}


def test_update_form_envoie_un_tableau_json_patch(calls, client):
    ops = [{"op": "replace", "path": "/settings/is_public", "value": True},
           {"op": "replace", "path": "/title", "value": "Live"}]
    assert client.update_form("f1", ops) is None
    c = calls[0]
    assert (c["method"], c["url"]) == ("PATCH", f"{US}/forms/f1")
    assert c["json"] == ops


@pytest.mark.parametrize("ops", [
    [],
    "not a list",
    [{"op": "add", "path": "/title", "value": "x"}],
    [{"op": "replace", "path": "/fields", "value": []}],
    [{"op": "replace", "path": "/title"}],
    [{"op": "replace", "path": "/title", "value": "x", "from": "/a"}],
])
def test_update_form_refuse_avant_lappel(calls, client, ops):
    with pytest.raises(ValueError):
        client.update_form("f1", ops)
    assert calls == []


def test_les_chemins_patch_sont_ceux_de_la_reference():
    assert "/settings/is_public" in PATCH_PATHS and "/title" in PATCH_PATHS
    assert not any(p.startswith("/fields") for p in PATCH_PATHS)


def test_delete_form_sans_corps_et_rend_none(calls, client):
    assert client.delete_form("f1") is None
    c = calls[0]
    assert (c["method"], c["url"]) == ("DELETE", f"{US}/forms/f1")
    assert "json" not in c


@pytest.mark.parametrize("bad", ["", ".", ".."])
def test_delete_form_refuse_un_id_vide_ou_relatif(calls, client, bad):
    with pytest.raises(ValueError):
        client.delete_form(bad)
    assert calls == []


def test_erreur_http_typee_sur_une_ecriture(calls, client):
    calls.responses.append(_Resp({"code": "FORBIDDEN"}, 403))
    with pytest.raises(UpstreamHTTPError) as exc:
        client.delete_form("f1")
    assert exc.value.status_code == 403 and exc.value.service == "typeform"


# --- Réponses --------------------------------------------------------------


def test_delete_responses_ids_dans_le_corps(calls, client):
    calls.responses.append(_Resp(None, 200))
    assert client.delete_responses("f1", ["r1", "r2"]) is None
    c = calls[0]
    assert (c["method"], c["url"]) == ("DELETE", f"{US}/forms/f1/responses")
    assert c["json"] == {"included_response_ids": ["r1", "r2"]}
    assert "params" not in c


@pytest.mark.parametrize("ids", [[], "r1,r2", ["r1", ""], ["r"] * 1001, None])
def test_delete_responses_refuse_avant_lappel(calls, client, ids):
    with pytest.raises(ValueError):
        client.delete_responses("f1", ids)
    assert calls == []


# --- Webhooks --------------------------------------------------------------

HOOK = {"id": "w1", "tag": "crm", "url": "https://h.example.com/x", "enabled": True,
        "event_types": {"form_response": True}, "secret": "s3cr3t", "verify_ssl": True,
        "form_id": "f1", "created_at": "2026-10-01T00:00:00Z", "updated_at": "2026-10-01T00:00:00Z"}


def test_list_webhooks_sans_secret(calls, client):
    calls.responses.append(_Resp({"items": [HOOK, {**HOOK, "tag": "b"}]}))
    out = client.list_webhooks("f1")
    assert (calls[0]["method"], calls[0]["url"]) == ("GET", f"{US}/forms/f1/webhooks")
    assert [w["tag"] for w in out["items"]] == ["crm", "b"]
    assert all("secret" not in w for w in out["items"])


def test_get_webhook_sans_secret_et_tag_echappe(calls, client):
    calls.responses.append(_Resp(HOOK))
    out = client.get_webhook("f1", "a/b")
    assert calls[0]["url"] == f"{US}/forms/f1/webhooks/a%2Fb"
    assert "secret" not in out and out["url"] == HOOK["url"]


def test_upsert_webhook_corps_et_secret_non_rendu(calls, client):
    calls.responses.append(_Resp(HOOK))
    out = client.upsert_webhook("f1", "crm", url="https://h.example.com/x", enabled=True,
                                event_types={"form_response": True}, secret="s3cr3t")
    c = calls[0]
    assert (c["method"], c["url"]) == ("PUT", f"{US}/forms/f1/webhooks/crm")
    assert c["json"] == {"url": "https://h.example.com/x", "enabled": True,
                         "event_types": {"form_response": True}, "secret": "s3cr3t"}
    assert "params" not in c
    assert "secret" not in out


@pytest.mark.parametrize("kwargs", [
    {"url": "http://h.example.com", "enabled": True},
    {"url": "https://h.example.com", "enabled": "yes"},
    {"url": "https://h.example.com", "enabled": True, "event_types": {}},
    {"url": "https://h.example.com", "enabled": True, "event_types": {"form_deleted": True}},
])
def test_upsert_webhook_refuse_avant_lappel(calls, client, kwargs):
    with pytest.raises(ValueError):
        client.upsert_webhook("f1", "crm", **kwargs)
    assert calls == []


def test_delete_webhook(calls, client):
    assert client.delete_webhook("f1", "crm") is None
    assert (calls[0]["method"], calls[0]["url"]) == ("DELETE", f"{US}/forms/f1/webhooks/crm")
    assert "json" not in calls[0]


def test_la_region_vaut_pour_les_ecritures(calls):
    TypeformClient(access_token="t", region="eu2").delete_webhook("f1", "crm")
    assert calls[0]["url"] == "https://api.typeform.eu/forms/f1/webhooks/crm"
