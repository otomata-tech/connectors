"""PayfitClient — verrouille le contrat HTTP construit par le client.

Mocke `requests.Session.request` : verbe + URL + params + corps, sans réseau ni
clé réelle. Cible ce qui pourrait dériver en silence : l'en-tête d'auth, la
résolution de l'id d'entreprise par introspection et son cache, la surface
(lecture seule, aucune méthode vers la paie), les chemins et filtres, les bornes
de pagination, le refus d'un identifiant qui réécrirait l'URL, et le fait qu'une
erreur d'introspection ne recopie jamais la réponse.
"""
import json

import pytest

from oto.tools.common import UpstreamHTTPError
from oto.tools.payfit import PayfitClient
from oto.tools.payfit import client as payfit_client

BASE = "https://partner-api.payfit.com"
INTROSPECT = "https://oauth.payfit.com/introspect"
KEY = "pf-test-key"
CO = "000000000000000000000c0c"
A = "000000000000000000000a0a"


class _Resp:
    def __init__(self, payload=None, status_code=200):
        self.status_code = status_code
        self._payload = payload
        self.headers = {"Content-Type": "application/json"}
        self.content = b"" if payload is None else json.dumps(payload).encode()
        self.text = self.content.decode()

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


@pytest.fixture(autouse=True)
def _cache_vide():
    payfit_client._COMPANY_IDS.clear()
    yield
    payfit_client._COMPANY_IDS.clear()


class _Seen(list):
    """The captured calls, plus `queued`: replies served first, in order."""


@pytest.fixture
def calls(monkeypatch):
    seen = _Seen()
    queued = []

    def _request(self, method, url, **kwargs):
        seen.append({"method": method, "url": url, "headers": dict(self.headers), **kwargs})
        if queued:
            return queued.pop(0)
        if url == INTROSPECT:
            return _Resp({"active": True, "scope": "x", "company_id": CO,
                          "token_type": "bearer"})
        return _Resp({"meta": {}})

    monkeypatch.setattr("requests.Session.request", _request)
    seen.queued = queued
    return seen


@pytest.fixture
def client():
    return PayfitClient(api_key=KEY)


METHODS = {
    "get_company_id", "get_company", "list_collaborators", "get_collaborator",
    "list_contracts", "get_contract", "list_absences",
}

_OUT_OF_SCOPE = ("payslip", "accounting", "payment", "health", "provident", "meal",
                 "income", "enrolment", "document", "create", "update", "delete",
                 "cancel", "declare")


def test_surface_is_exactly_the_read_scope():
    public = {n for n in dir(PayfitClient)
              if not n.startswith("_") and callable(getattr(PayfitClient, n))}
    assert public == METHODS
    for name in METHODS:
        assert not any(m in name for m in _OUT_OF_SCOPE), name


def test_company_id_comes_from_introspection_with_the_key_in_body(calls, client):
    assert client.get_company_id() == CO
    call = calls[0]
    assert (call["method"], call["url"]) == ("POST", INTROSPECT)
    assert call["json"] == {"token": KEY}
    assert call["headers"]["Authorization"] == f"Bearer {KEY}"
    assert "params" not in call


def test_company_id_is_cached_process_wide_by_key(calls):
    PayfitClient(api_key=KEY).get_company()
    PayfitClient(api_key=KEY).get_company()
    assert [c["url"] for c in calls].count(INTROSPECT) == 1
    PayfitClient(api_key="pf-other-key").get_company()
    assert [c["url"] for c in calls].count(INTROSPECT) == 2
    assert KEY not in json.dumps(list(payfit_client._COMPANY_IDS))


@pytest.mark.parametrize("fn,args,kwargs,path,params", [
    ("get_company", (), {}, "", {}),
    ("list_collaborators", (), {}, "/collaborators", {"maxResults": 50}),
    ("list_collaborators", (), {"limit": 10, "cursor": "tok", "email": "a@exemple.test"},
     "/collaborators", {"maxResults": 10, "nextPageToken": "tok",
                        "email": "a@exemple.test"}),
    ("get_collaborator", (A,), {}, f"/collaborators/{A}", {}),
    ("list_contracts", (), {}, "/contracts", {"maxResults": 50}),
    ("list_contracts", (), {"fr": True, "include_in_progress": False}, "/contracts-fr",
     {"maxResults": 50, "includeInProgressContracts": "false"}),
    ("get_contract", (A,), {}, f"/contracts/{A}", {}),
    ("get_contract", (A,), {"fr": True}, f"/contracts-fr/{A}", {}),
    ("list_absences", (), {}, "/absences", {"maxResults": 50}),
    ("list_absences", (), {"contract_id": A, "status": ["approved", "pending_approval"],
                           "begin_date": "2026-01-01", "end_date": "2026-01-31"},
     "/absences", {"maxResults": 50, "contractId": A,
                   "status": "approved,pending_approval",
                   "beginDate": "2026-01-01", "endDate": "2026-01-31"}),
])
def test_read_paths_and_params(calls, client, fn, args, kwargs, path, params):
    getattr(client, fn)(*args, **kwargs)
    call = calls[-1]
    assert call["method"] == "GET"
    assert call["url"] == f"{BASE}/companies/{CO}{path}"
    assert call["params"] == params
    assert call["headers"]["Authorization"] == f"Bearer {KEY}"
    assert KEY not in json.dumps(call["params"])


def test_contracts_fr_never_sends_the_fields_parameter(calls, client):
    client.list_contracts(fr=True)
    assert "fields" not in calls[-1]["params"]


@pytest.mark.parametrize("bad", ["", None, "../absences", f"{A}/x", f"{A}?x=1", "a b"])
def test_an_invalid_identifier_never_reaches_the_url(calls, client, bad):
    with pytest.raises(ValueError, match="invalide"):
        client.get_collaborator(bad)
    assert not [c for c in calls if c["method"] == "GET"]


@pytest.mark.parametrize("limit", [0, 51])
def test_pagination_bounds(calls, client, limit):
    with pytest.raises(ValueError, match="limit"):
        client.list_collaborators(limit=limit)
    assert not [c for c in calls if c["method"] == "GET"]


@pytest.mark.parametrize("resp", [
    _Resp({"error": "bad", "echo": KEY}, status_code=401),
    _Resp({"active": False, "token": KEY}),
    _Resp({"active": True}),
])
def test_introspection_failure_never_copies_the_answer(calls, client, resp):
    calls.queued.append(resp)
    with pytest.raises(UpstreamHTTPError) as exc:
        client.get_company()
    assert exc.value.status_code == 401
    assert KEY not in str(exc.value) and KEY not in json.dumps(exc.value.body)
    assert not payfit_client._COMPANY_IDS


def test_error_raises_with_status_code(calls, client):
    client.get_company_id()
    calls.queued.append(_Resp({"error": "Forbidden"}, status_code=403))
    with pytest.raises(UpstreamHTTPError) as exc:
        client.list_absences()
    assert exc.value.status_code == 403
