"""PayfitClient — verrouille le contrat HTTP construit par le client.

Mocke `requests.Session.request` : verbe + URL + params + corps, sans réseau ni
clé réelle. Cible ce qui pourrait dériver en silence : l'en-tête d'auth, la
résolution de l'id d'entreprise par introspection et son cache, la SURFACE (les
27 méthodes, figées), les chemins et filtres, les bornes de pagination, le format
de mois `AAAAMM`, le refus d'un identifiant qui réécrirait l'URL, le fait qu'un
corps binaire ne passe jamais par `.json()`, et le fait qu'une erreur
d'introspection ne recopie jamais la réponse.
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
B = "000000000000000000000b0b"
MOIS = "202601"


class _Resp:
    def __init__(self, payload=None, status_code=200, *, raw=None, content_type=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = {"Content-Type": content_type or "application/json"}
        if raw is not None:
            self.content = raw
        else:
            self.content = b"" if payload is None else json.dumps(payload).encode()
        self.text = self.content.decode("utf-8", "replace")

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


# La surface EXACTE : ajouter une méthode est un geste, pas un effet de bord.
METHODS = {
    "get_company_id", "get_company", "get_payroll_status",
    "list_accounting_entries", "get_accounting_export", "get_payment_file",
    "get_document", "list_income_tax_documents", "list_auto_enrolment_documents",
    "list_health_insurance_contracts", "list_provident_fund_contracts",
    "list_collaborators", "get_collaborator", "create_collaborator",
    "list_payslips", "get_payslip", "list_meal_vouchers",
    "list_contracts", "get_contract", "create_contract", "list_worked_time",
    "set_health_insurance", "set_provident_fund",
    "request_health_insurance_regularization",
    "list_absences", "create_absence", "cancel_absence",
}

# Ce que l'API PayFit N'EXPOSE PAS : aucune méthode ne doit prétendre le servir.
# Un connecteur qui invente un endpoint échoue en prod, chez le client, sur une
# question qu'il croyait avoir posée. Cf. la docstring de `client.py`.
INEXISTANT = ("dsn", "planning", "schedule", "balance", "solde", "counter",
              "expense", "note_de_frais", "amendment", "avenant", "payslip_line",
              "ytd", "cumul", "variable_payroll", "evp", "terminate", "update_contract")


def test_surface_is_exactly_the_documented_api():
    public = {n for n in dir(PayfitClient)
              if not n.startswith("_") and callable(getattr(PayfitClient, n))}
    assert public == METHODS


def test_no_method_claims_an_endpoint_the_api_does_not_have():
    for name in METHODS:
        assert not any(m in name for m in INEXISTANT), (
            f"{name} : l'API PayFit n'expose rien de tel (spec lue le 2026-09-17) — "
            "une méthode qui le prétend fabrique une capacité inexistante.")


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


# --- lecture ------------------------------------------------------------------

@pytest.mark.parametrize("fn,args,kwargs,path,params", [
    ("get_company", (), {}, "", {}),
    ("get_payroll_status", (), {"date": MOIS}, "/payroll-status", {"date": MOIS}),
    ("list_accounting_entries", (), {"date": MOIS}, "/accounting-v2", {"date": MOIS}),
    ("list_income_tax_documents", (), {}, "/income-taxes-documents", {}),
    ("list_auto_enrolment_documents", (), {}, "/auto-enrolment-documents", {}),
    ("list_health_insurance_contracts", (), {}, "/health-insurance-contracts", {}),
    ("list_provident_fund_contracts", (), {}, "/provident-fund-contracts", {}),
    ("list_collaborators", (), {}, "/collaborators", {"maxResults": 50}),
    ("list_collaborators", (), {"limit": 10, "cursor": "tok", "email": "a@exemple.test"},
     "/collaborators", {"maxResults": 10, "nextPageToken": "tok",
                        "email": "a@exemple.test"}),
    ("get_collaborator", (A,), {}, f"/collaborators/{A}", {}),
    ("list_payslips", (A,), {}, f"/collaborators/{A}/payslips", {}),
    ("list_meal_vouchers", (MOIS,), {}, "/collaborators/meal-vouchers",
     {"date": MOIS, "maxResults": 50}),
    ("list_contracts", (), {}, "/contracts", {"maxResults": 50}),
    ("list_contracts", (), {"fr": True, "include_in_progress": False}, "/contracts-fr",
     {"maxResults": 50, "includeInProgressContracts": "false"}),
    ("get_contract", (A,), {}, f"/contracts/{A}", {}),
    ("get_contract", (A,), {"fr": True}, f"/contracts-fr/{A}", {}),
    ("list_worked_time", (MOIS,), {}, "/contracts/time",
     {"date": MOIS, "maxResults": 50}),
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


def test_the_french_company_lives_under_another_collection_root(calls, client):
    client.get_company(fr=True)
    assert calls[-1]["url"] == f"{BASE}/companies-fr/{CO}"


def test_contracts_fr_never_sends_the_fields_parameter(calls, client):
    client.list_contracts(fr=True)
    assert "fields" not in calls[-1]["params"]


# --- corps binaires -----------------------------------------------------------

@pytest.mark.parametrize("fn,args,path,mime", [
    ("get_accounting_export", (MOIS,), "/accounting", "text/csv"),
    ("get_payment_file", (MOIS,), "/payment-files", "application/octet-stream"),
    ("get_document", (A,), f"/documents/{A}", "application/pdf"),
])
def test_a_binary_body_is_never_parsed_as_json(calls, client, fn, args, path, mime):
    """Un `.json()` sur un PDF transformerait une réponse VALIDE en panne."""
    client.get_company_id()          # l'introspection d'abord : sinon elle consomme
    pdf = b"%PDF-1.4\n\x00binaire"  # la réponse mise en file pour le GET binaire
    calls.queued.append(_Resp(raw=pdf, content_type=mime))
    out = getattr(client, fn)(*args)
    assert out["data"] == pdf
    assert out["mimetype"] == mime
    assert out["filename"]
    assert calls[-1]["url"] == f"{BASE}/companies/{CO}{path}"


def test_the_payslip_pdf_needs_its_three_identifiers(calls, client):
    client.get_company_id()
    calls.queued.append(_Resp(raw=b"%PDF-1.4", content_type="application/pdf"))
    client.get_payslip(A, B, "42")
    assert calls[-1]["url"] == (
        f"{BASE}/companies/{CO}/collaborators/{A}/contracts/{B}/payslips/42")


def test_the_served_content_type_wins_over_the_announced_one(calls, client):
    client.get_company_id()
    calls.queued.append(_Resp(raw=b"a;b;c", content_type="text/csv; charset=utf-8"))
    assert client.get_payment_file(MOIS)["mimetype"] == "text/csv"


# --- écriture -----------------------------------------------------------------

def test_create_collaborator_sends_only_what_was_given(calls, client):
    client.create_collaborator(first_name="Ada", last_name="Exemple",
                               personal_email="ada@exemple.test",
                               gender="FEMALE")
    call = calls[-1]
    assert call["method"] == "POST"
    assert call["url"] == f"{BASE}/companies/{CO}/collaborators"
    assert call["json"] == {"firstName": "Ada", "lastName": "Exemple",
                            "personalEmail": "ada@exemple.test", "gender": "FEMALE"}


def test_create_contract_hangs_under_its_collaborator(calls, client):
    client.create_contract(A, job_title="Infirmier", start_date="2026-02-01")
    call = calls[-1]
    assert (call["method"], call["url"]) == (
        "POST", f"{BASE}/companies/{CO}/collaborators/{A}/contracts")
    assert call["json"] == {"jobTitle": "Infirmier", "startDate": "2026-02-01"}


def test_create_absence_builds_both_day_moments(calls, client):
    client.create_absence(contract_id=A, absence_type="fr_conges_payes",
                          start_date="2026-02-02", end_date="2026-02-06")
    call = calls[-1]
    assert (call["method"], call["url"]) == ("POST", f"{BASE}/companies/{CO}/absences")
    assert call["json"] == {
        "contractId": A, "type": "fr_conges_payes",
        "startDate": {"date": "2026-02-02", "moment": "beginning-of-day"},
        "endDate": {"date": "2026-02-06", "moment": "end-of-day"}}


def test_an_unknown_moment_is_refused_before_the_network(calls, client):
    client.get_company_id()
    before = len(calls)
    with pytest.raises(ValueError, match="start_moment"):
        client.create_absence(contract_id=A, absence_type="fr_rtt",
                              start_date="2026-02-02", end_date="2026-02-02",
                              start_moment="matin")
    assert len(calls) == before


def test_cancel_absence_carries_its_comment_in_the_body(calls, client):
    client.cancel_absence(A, comment="doublon")
    call = calls[-1]
    assert (call["method"], call["url"]) == (
        "DELETE", f"{BASE}/companies/{CO}/absences/{A}")
    assert call["json"] == {"comment": "doublon"}


def test_cancel_absence_without_comment_sends_no_body(calls, client):
    client.cancel_absence(A)
    assert calls[-1]["json"] is None


@pytest.mark.parametrize("fn,kwargs,verb,suffix,body", [
    ("set_health_insurance", {"health_insurance_contract_ids": [A, B],
                              "employee_is_exempted": False},
     "PUT", "/health-insurance",
     {"healthInsuranceContractIds": [A, B], "employeeIsExempted": False}),
    ("set_provident_fund", {"provident_fund_contract_ids": [A]},
     "PUT", "/provident-fund", {"providentFundContractIds": [A]}),
    ("request_health_insurance_regularization",
     {"health_insurance_contract_ids": [A], "effective_date": "2026-01-01"},
     "POST", "/regularization",
     {"healthInsuranceContractIds": [A], "effectiveDate": "2026-01-01"}),
])
def test_insurance_writes_hang_under_the_french_contract(calls, client, fn, kwargs,
                                                         verb, suffix, body):
    getattr(client, fn)(B, **kwargs)
    call = calls[-1]
    assert (call["method"], call["url"]) == (
        verb, f"{BASE}/companies/{CO}/contracts-fr/{B}{suffix}")
    assert call["json"] == body


def test_employee_is_exempted_false_is_a_VALUE_not_an_omission(calls, client):
    """`False` est une décision (« non dispensé »), pas un argument absent : un
    `clean` qui filtrerait sur la vérité le ferait disparaître du corps."""
    client.set_health_insurance(B, health_insurance_contract_ids=[A],
                                employee_is_exempted=False)
    assert calls[-1]["json"]["employeeIsExempted"] is False


@pytest.mark.parametrize("bad", ["", [], "not-a-list"])
def test_an_empty_or_scalar_id_list_is_refused(calls, client, bad):
    client.get_company_id()
    before = len(calls)
    with pytest.raises(ValueError, match="health_insurance_contract_ids"):
        client.set_health_insurance(B, health_insurance_contract_ids=bad)
    assert len(calls) == before


# --- gardes -------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["", None, "../absences", f"{A}/x", f"{A}?x=1", "a b"])
def test_an_invalid_identifier_never_reaches_the_url(calls, client, bad):
    with pytest.raises(ValueError, match="invalide"):
        client.get_collaborator(bad)
    assert not [c for c in calls if c["method"] == "GET"]


@pytest.mark.parametrize("bad", ["2026-01", "202613", "janvier", "", "20261", None])
def test_a_month_that_is_not_yyyymm_is_refused_before_the_network(calls, client, bad):
    """`2026-01` est la forme qu'un humain écrit spontanément, et la seule que
    PayFit refuse — son 400 ne nomme aucun champ."""
    with pytest.raises(ValueError, match="AAAAMM"):
        client.list_accounting_entries(bad)
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


def test_a_write_that_fails_raises_on_the_status_not_the_text(calls, client):
    client.get_company_id()
    calls.queued.append(_Resp({"error": "absence overlaps"}, status_code=422))
    with pytest.raises(UpstreamHTTPError) as exc:
        client.create_absence(contract_id=A, absence_type="fr_rtt",
                              start_date="2026-02-02", end_date="2026-02-02")
    assert exc.value.status_code == 422


def test_no_secret_ever_travels_in_a_query_string(calls, client):
    client.list_collaborators()
    client.create_absence(contract_id=A, absence_type="fr_rtt",
                          start_date="2026-02-02", end_date="2026-02-02")
    for call in calls:
        assert KEY not in json.dumps(call.get("params") or {})
        assert KEY not in call["url"]
