"""NextmotionClient — verrouille le contrat HTTP construit par le client.

Mocke `requests.Session.request` : verbe + URL + params + corps, sans réseau ni
clé réelle. Cible ce qui pourrait dériver en silence : l'en-tête d'auth, la
surface (aucune méthode vers un endpoint médical), les chemins et filtres, les
bornes de pagination, le refus d'un identifiant qui n'est pas un UUID, et le
décodage d'une réponse 204 / d'une erreur.
"""
import json

import pytest

from oto.tools.common import UpstreamHTTPError
from oto.tools.nextmotion import NextmotionClient

BASE = "https://api.nextmotion.net/open_api"
A = "00000000-0000-4000-8000-00000000000a"
B = "00000000-0000-4000-8000-00000000000b"


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


class _Seen(list):
    """The captured calls, plus `responses`: queued replies, served in order."""


@pytest.fixture
def calls(monkeypatch):
    seen = _Seen()
    responses = []

    def _request(self, method, url, **kwargs):
        seen.append({"method": method, "url": url, "headers": dict(self.headers), **kwargs})
        return responses.pop(0) if responses else _Resp({"data": []})

    monkeypatch.setattr("requests.Session.request", _request)
    seen.responses = responses
    return seen


@pytest.fixture
def client():
    return NextmotionClient(api_key="nm-test")


#: La surface entière. Aucune méthode ne vise un dossier patient, un antécédent,
#: une photo, une ordonnance, un consentement, un soin ou une consultation.
METHODS = {
    "get_me", "list_clinics", "list_doctors", "get_doctor",
    "list_appointments", "get_appointment", "reschedule_appointment",
    "delete_appointment", "search_time_slots",
    "list_visit_types", "get_visit_type",
    "list_visit_type_categories", "get_visit_type_category",
    "list_sub_visit_types", "get_sub_visit_type",
    "list_treatment_types", "get_treatment_type",
    "list_treatment_pricings", "get_treatment_pricing",
    "list_quotes", "get_quote", "list_invoices", "get_invoice",
}

_MEDICAL_MARKERS = ("patient", "medical", "prescription", "consent", "media",
                    "photo", "consultation")


def test_surface_is_exactly_the_administrative_scope():
    public = {n for n in dir(NextmotionClient)
              if not n.startswith("_") and callable(getattr(NextmotionClient, n))}
    assert public == METHODS


def test_no_method_targets_a_medical_resource():
    for name in METHODS:
        assert not any(m in name for m in _MEDICAL_MARKERS), name


def test_api_key_goes_in_bearer_header_never_in_params(calls, client):
    client.get_me()
    call = calls[0]
    assert call["headers"]["Authorization"] == "Bearer nm-test"
    assert "nm-test" not in json.dumps(call.get("params") or {})
    assert call["url"] == f"{BASE}/v4/users/me"


@pytest.mark.parametrize("fn,args,kwargs,method,path,params", [
    ("list_clinics", (), {}, "GET", "/v4/clinics", {"limit": 50, "offset": 0}),
    ("list_doctors", (A,), {"limit": 10, "offset": 20}, "GET",
     f"/v4/clinics/{A}/doctors", {"limit": 10, "offset": 20}),
    ("get_doctor", (A,), {}, "GET", f"/v4/doctors/{A}", {}),
    ("list_appointments", (A,), {"date": "2026-01-02", "patient_id": B}, "GET",
     f"/v4/clinics/{A}/calendar_appointments",
     {"limit": 50, "offset": 0, "date": "2026-01-02", "patient": B}),
    ("get_appointment", (A,), {}, "GET", f"/v4/calendar_appointments/{A}", {}),
    ("delete_appointment", (A,), {}, "DELETE", f"/v4/calendar_appointments/{A}", {}),
    ("list_visit_types", (A,), {"visit_type_category_id": B}, "GET",
     f"/v4/clinics/{A}/visit_types",
     {"limit": 50, "offset": 0, "visit_type_category": B}),
    ("get_visit_type", (A,), {}, "GET", f"/v4/visit_types/{A}", {}),
    ("list_visit_type_categories", (A,), {}, "GET",
     f"/v4/clinics/{A}/visit_type_categories", {"limit": 50, "offset": 0}),
    ("get_visit_type_category", (A,), {}, "GET", f"/v4/visit_type_categories/{A}", {}),
    ("list_sub_visit_types", (A,), {"visit_type_id": B}, "GET",
     f"/v4/clinics/{A}/sub_visit_types", {"limit": 50, "offset": 0, "visit_type": B}),
    ("get_sub_visit_type", (A,), {}, "GET", f"/v4/sub_visit_types/{A}", {}),
    ("list_treatment_types", (A,), {"search": "zzz"}, "GET",
     f"/v4/clinics/{A}/treatment_types", {"limit": 50, "offset": 0, "search": "zzz"}),
    ("get_treatment_type", (A,), {}, "GET", f"/v4/treatment_types/{A}", {}),
    ("list_treatment_pricings", (A,), {"treatment_type_id": B}, "GET",
     f"/v4/clinics/{A}/treatment_pricings",
     {"limit": 50, "offset": 0, "treatment_type": B}),
    ("get_treatment_pricing", (A,), {}, "GET", f"/v4/treatment_pricings/{A}", {}),
    ("list_quotes", (A,), {"patient_id": B}, "GET", f"/v4/clinics/{A}/quotes",
     {"limit": 50, "offset": 0, "patient": B}),
    ("get_quote", (A,), {}, "GET", f"/v4/quotes/{A}", {}),
    ("list_invoices", (A,), {}, "GET", f"/v4/clinics/{A}/invoices",
     {"limit": 50, "offset": 0}),
    ("get_invoice", (A,), {}, "GET", f"/v4/invoices/{A}", {}),
])
def test_read_paths_and_params(calls, client, fn, args, kwargs, method, path, params):
    getattr(client, fn)(*args, **kwargs)
    call = calls[0]
    assert (call["method"], call["url"], call["params"]) == (method, f"{BASE}{path}", params)
    assert call["json"] is None


def test_omitted_filters_are_not_sent(calls, client):
    client.list_appointments(A)
    assert calls[0]["params"] == {"limit": 50, "offset": 0}


def test_reschedule_posts_both_required_fields(calls, client):
    client.reschedule_appointment(A, visit_type_opening_hour_id=B,
                                  time_slot="2026-01-02T10:00:00+01:00")
    call = calls[0]
    assert call["method"] == "POST"
    assert call["url"] == f"{BASE}/v4/calendar_appointments/{A}/reschedule"
    assert call["json"] == {"visit_type_opening_hour": B,
                            "time_slot": "2026-01-02T10:00:00+01:00"}


def test_reschedule_refuses_an_empty_time_slot(calls, client):
    with pytest.raises(ValueError, match="time_slot"):
        client.reschedule_appointment(A, visit_type_opening_hour_id=B, time_slot="")
    assert not calls


def test_time_slot_search_is_a_post_with_only_given_fields(calls, client):
    client.search_time_slots(A, start_date="2026-01-01", doctor_id=B)
    call = calls[0]
    assert call["method"] == "POST"
    assert call["url"] == f"{BASE}/v4/clinics/{A}/visit_types/opening_hours"
    assert call["json"] == {"start_date": "2026-01-01", "doctor_id": B}


@pytest.mark.parametrize("bad", ["", None, "42", f"{A}/../x", "../users/me", f"{A}?x=1", f"{A}\n"])
def test_a_non_uuid_identifier_never_reaches_the_url(calls, client, bad):
    with pytest.raises(ValueError, match="UUID"):
        client.delete_appointment(bad)
    with pytest.raises(ValueError, match="UUID"):
        client.list_appointments(A, patient_id=bad if bad is not None else "")
    assert not calls


@pytest.mark.parametrize("limit,offset", [(0, 0), (101, 0), (50, -1)])
def test_pagination_bounds(calls, client, limit, offset):
    with pytest.raises(ValueError):
        client.list_clinics(limit=limit, offset=offset)
    assert not calls


def test_204_returns_none(calls, client):
    calls.responses.append(_Resp(None, status_code=204))
    assert client.delete_appointment(A) is None


def test_error_envelope_raises_with_status_code(calls, client):
    calls.responses.append(_Resp(
        {"errors": [{"code": "non_employee_access_denied", "message": "x"}]},
        status_code=403))
    with pytest.raises(UpstreamHTTPError) as exc:
        client.list_doctors(A)
    assert exc.value.status_code == 403
