"""InqomClient — token (password grant, secrets in the body, process-wide cache,
refusal typed 401), Bearer transport, renewal on 401, 429 backoff, endpoint
contract (paths, query params, date validation) and posting entries.

Transport stubbed (`requests.post` for the token, `Session.request` for the
API): no network, no real credential.
"""
import json

import pytest

from oto.tools.common import UpstreamHTTPError
from oto.tools.inqom import InqomAuthError, InqomClient
from oto.tools.inqom import auth as inqom_auth
from oto.tools.inqom import client as inqom_client


class _Resp:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self._payload = payload
        self.content = json.dumps(payload).encode() if payload is not None else b""
        self.headers = {"Content-Type": "application/json"}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    @property
    def text(self):
        return json.dumps(self._payload)


@pytest.fixture(autouse=True)
def _cache_vide():
    inqom_auth._TOKEN_CACHE.clear()
    yield
    inqom_auth._TOKEN_CACHE.clear()


@pytest.fixture
def token(monkeypatch):
    """Token endpoint stub: records each call; queue responses in `replies`."""
    calls, replies = [], []

    def fake_post(url, data=None, params=None, timeout=None, **kw):
        calls.append({"url": url, "data": data, "params": params})
        if replies:
            return replies.pop(0)
        return _Resp({"access_token": f"tok-{len(calls)}", "expires_in": 3600,
                      "token_type": "Bearer"})

    monkeypatch.setattr(inqom_auth.requests, "post", fake_post)
    return calls, replies


@pytest.fixture
def api(monkeypatch):
    """API stub on `Session.request`; queue responses in `replies`."""
    calls, replies = [], []

    def fake_request(self, method, url, headers=None, params=None, json=None, timeout=None):
        calls.append({"method": method, "url": url, "headers": headers,
                      "params": params, "json": json})
        if replies:
            return replies.pop(0)
        return _Resp([])

    monkeypatch.setattr(inqom_client.requests.Session, "request", fake_request)
    monkeypatch.setattr(inqom_client.time, "sleep", lambda s: None)
    return calls, replies


@pytest.fixture
def c():
    return InqomClient(client_id="app", client_secret="app-secret",
                       username="system@exemple.fr", password="pw")


# --- token -----------------------------------------------------------------

def test_le_jeton_part_en_corps_jamais_en_query(c, token, api):
    c.list_journals(12)
    (call,) = token[0]
    assert call["url"] == InqomClient.TOKEN_URL
    assert call["params"] is None
    assert call["data"] == {
        "grant_type": "password", "client_id": "app", "client_secret": "app-secret",
        "username": "system@exemple.fr", "password": "pw", "scope": "openid apidata"}
    assert api[0][0]["headers"]["Authorization"] == "Bearer tok-1"


def test_le_cache_de_jeton_survit_a_une_nouvelle_instance(c, token, api):
    c.list_journals(12)
    InqomClient(client_id="app", client_secret="app-secret",
                username="system@exemple.fr", password="pw").list_journals(12)
    assert len(token[0]) == 1


def test_deux_credentials_ne_partagent_pas_de_jeton(c, token, api):
    c.list_journals(12)
    InqomClient(client_id="app", client_secret="app-secret",
                username="autre@exemple.fr", password="pw").list_journals(12)
    assert len(token[0]) == 2


def test_la_cle_de_cache_ne_contient_aucun_secret_en_clair(c, token, api):
    c.list_journals(12)
    (k,) = inqom_auth._TOKEN_CACHE
    assert "pw" not in k and "app-secret" not in k


@pytest.mark.parametrize("status,error", [(400, "invalid_grant"), (400, "invalid_client"),
                                          (401, None)])
def test_un_refus_du_serveur_d_identite_est_un_401_type(c, token, api, status, error):
    token[1].append(_Resp({"error": error} if error else None, status_code=status))
    with pytest.raises(InqomAuthError) as e:
        c.list_journals(12)
    assert e.value.status_code == 401
    assert "pw" not in str(e.value) and "app-secret" not in str(e.value)
    assert api[0] == []


def test_une_panne_du_serveur_d_identite_garde_son_statut(c, token, api):
    token[1].append(_Resp({"error": "server_error", "detail": "x"}, status_code=503))
    with pytest.raises(UpstreamHTTPError) as e:
        c.list_journals(12)
    assert e.value.status_code == 503 and not isinstance(e.value, InqomAuthError)
    assert e.value.body == {"error": "server_error"}


# --- transport ---------------------------------------------------------------

def test_un_401_de_l_api_renouvelle_le_jeton_une_seule_fois(c, token, api):
    api[1].extend([_Resp({"Message": "no"}, 401), _Resp([{"Id": 1}])])
    assert c.list_journals(12) == [{"Id": 1}]
    assert len(token[0]) == 2
    assert api[0][1]["headers"]["Authorization"] == "Bearer tok-2"


def test_un_401_persistant_remonte(c, token, api):
    api[1].extend([_Resp({"Message": "no"}, 401), _Resp({"Message": "no"}, 401)])
    with pytest.raises(UpstreamHTTPError) as e:
        c.list_journals(12)
    assert e.value.status_code == 401


def test_un_429_est_rejoue_puis_remonte(c, token, api):
    api[1].extend([_Resp({}, 429)] * 5)
    with pytest.raises(UpstreamHTTPError) as e:
        c.list_journals(12)
    assert e.value.status_code == 429
    assert len(api[0]) == 5


def test_un_404_remonte_avec_son_statut(c, token, api):
    api[1].append(_Resp({"Message": "not found"}, 404))
    with pytest.raises(UpstreamHTTPError) as e:
        c.get_dossier(999)
    assert e.value.status_code == 404


# --- endpoints ---------------------------------------------------------------

@pytest.mark.parametrize("call,method,path,params", [
    (lambda c: c.list_companies(), "GET", "/provisioning/users/internal-access", {}),
    (lambda c: c.list_dossiers(7), "GET", "/provisioning/companies/7/accounting-folders", {}),
    (lambda c: c.get_dossier(12), "GET", "/v1/dossiers/12/dossier-permanent/general", {}),
    (lambda c: c.list_accounting_periods(12), "GET", "/v1/dossiers/12/accounting-periods", {}),
    (lambda c: c.list_journals(12), "GET", "/v1/dossiers/12/journals", {}),
    (lambda c: c.list_accounts(12, number_prefix="401", account_type="Impactable"),
     "GET", "/v1/dossiers/12/accounts",
     {"accountNumberPrefix": "401", "accountType": "Impactable"}),
    (lambda c: c.list_balances(12, "2026-01-01", "2026-12-31", account_numbers=["512000"],
                               balance_scope="All"),
     "GET", "/v1/dossiers/12/balances",
     {"startDate": "2026-01-01", "endDate": "2026-12-31", "accountNumbers": ["512000"],
      "balanceScope": "All"}),
    (lambda c: c.count_entry_lines(12, "2026-01-01", "2026-01-31"),
     "GET", "/v1/dossiers/12/entry-lines/count",
     {"startDate": "2026-01-01", "endDate": "2026-01-31"}),
    (lambda c: c.list_entry_lines(12, "2026-01-01", "2026-01-31", 2, journal_id=3),
     "GET", "/v1/dossiers/12/entry-lines",
     {"startDate": "2026-01-01", "endDate": "2026-01-31", "pageNumber": 2, "journalId": 3}),
    (lambda c: c.get_accounting_document(12, 55), "GET",
     "/v1/dossiers/12/accounting-documents/55", {}),
])
def test_chaque_methode_appelle_son_endpoint(c, token, api, call, method, path, params):
    call(c)
    (req,) = api[0]
    assert req["method"] == method
    assert req["url"] == InqomClient.API_BASE + path
    assert req["params"] == params


def test_create_entries_poste_la_liste_en_json(c, token, api):
    entries = [{"JournalId": 3, "Date": "2026-01-15", "Lines": [
        {"AccountNumber": "606100", "Label": "x", "Currency": "EUR", "DebitAmount": 10},
        {"AccountNumber": "401000", "Label": "x", "Currency": "EUR", "CreditAmount": 10}]}]
    api[1].append(_Resp([{"Id": 99, "Lines": []}]))
    assert c.create_entries(12, entries) == [{"Id": 99, "Lines": []}]
    (req,) = api[0]
    assert (req["method"], req["url"]) == ("POST", InqomClient.API_BASE + "/v1/dossiers/12/entries")
    assert req["json"] == entries


def test_create_entries_refuse_une_liste_vide_sans_appel(c, token, api):
    with pytest.raises(ValueError, match="entries"):
        c.create_entries(12, [])
    assert api[0] == [] and token[0] == []


@pytest.mark.parametrize("bad", ["01/01/2026", "2026-1-1", "", None])
def test_une_date_mal_formee_est_refusee_avant_tout_appel(c, token, api, bad):
    with pytest.raises(ValueError, match="start_date"):
        c.list_balances(12, bad, "2026-12-31")
    assert api[0] == []


def test_un_choix_hors_liste_est_refuse(c, token, api):
    with pytest.raises(ValueError, match="balance_scope"):
        c.list_balances(12, "2026-01-01", "2026-12-31", balance_scope="Everything")
    with pytest.raises(ValueError, match="page_number"):
        c.list_entry_lines(12, "2026-01-01", "2026-01-31", 0)
    assert api[0] == []
