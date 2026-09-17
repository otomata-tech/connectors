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


#: La surface entière. Aucune méthode ne vise un dossier ou une liste de patients, un
#: antécédent, une photo, une ordonnance, un consentement, un soin, une consultation,
#: une visite, un questionnaire de santé ou le chat.
METHODS = {
    "get_me", "list_clinics", "list_clinic_features", "list_doctors", "get_doctor",
    "list_appointments", "get_appointment", "reschedule_appointment",
    "delete_appointment", "search_time_slots",
    "list_appointment_requests", "get_appointment_request", "list_calendar_journeys",
    "list_calendar_absences", "get_calendar_absence",
    "list_calendar_opening_hours", "get_calendar_opening_hour",
    "list_appointment_rooms", "get_appointment_room",
    "list_appointment_devices", "get_appointment_device",
    "list_visit_types", "get_visit_type",
    "list_visit_type_categories", "get_visit_type_category",
    "list_sub_visit_types", "get_sub_visit_type",
    "list_treatment_types", "get_treatment_type",
    "list_treatment_pricings", "get_treatment_pricing",
    "list_treatment_pricing_distributions",
    "list_treatment_packages", "get_treatment_package", "list_treatment_package_items",
    "list_treatment_package_distributions",
    "list_accounting_distributions", "get_accounting_distribution",
    "list_global_products",
    "list_quotes", "get_quote", "list_invoices", "get_invoice",
    "list_payments", "get_payment", "list_payment_mediums", "get_payment_medium",
    "get_appointment_income_statistics", "list_treatment_type_statistics",
    "list_treatment_type_income_statistics", "get_patient_stats",
    "list_products", "get_product",
    "list_leads", "get_lead", "list_object_labels",
    "list_communication_templates", "get_communication_template",
    "list_document_templates", "get_document_template",
    "list_webhooks", "get_webhook",
}

_MEDICAL_MARKERS = ("patient", "medical", "prescription", "consent", "media",
                    "photo", "consultation", "treatments", "visit_record", "survey",
                    "chat", "contact")

#: La seule méthode par patient, voulue : des totaux financiers et des dates de visite,
#: par id. Ajouter un nom ici est une décision de périmètre, pas un contournement.
_PER_PATIENT_ADMIN = {"get_patient_stats"}


def test_surface_is_exactly_the_administrative_scope():
    public = {n for n in dir(NextmotionClient)
              if not n.startswith("_") and callable(getattr(NextmotionClient, n))}
    assert public == METHODS


def test_no_method_targets_a_medical_resource():
    for name in METHODS - _PER_PATIENT_ADMIN:
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
    ("list_products", (A,), {"search": "zzz", "stock_state": "low",
                             "expiring_within_days": 30, "order": "-stock_level"},
     "GET", f"/v4/clinics/{A}/products",
     {"limit": 50, "offset": 0, "search": "zzz", "stock_state": "low",
      "expiring_within_days": 30, "order": "-stock_level"}),
    ("list_products", (A,), {}, "GET", f"/v4/clinics/{A}/products",
     {"limit": 50, "offset": 0}),
    ("get_product", (A,), {}, "GET", f"/v4/products/{A}", {}),
    ("list_clinic_features", (A,), {}, "GET", f"/v4/clinics/{A}/features",
     {"limit": 50, "offset": 0}),
    ("list_appointment_requests", (A,), {"status": "new"}, "GET",
     f"/v4/clinics/{A}/appointment_requests", {"limit": 50, "offset": 0, "status": "new"}),
    ("get_appointment_request", (A,), {}, "GET", f"/v4/appointment_requests/{A}", {}),
    ("list_calendar_journeys", (A,), {"start_date": "2026-01-01", "include_ongoing": False,
                                      "doctor_ids": [A, B], "patient_id": B,
                                      "order": "-start_time", "status": "finished"},
     "GET", f"/v4/clinics/{A}/calendar_journeys",
     {"limit": 50, "offset": 0, "start_date": "2026-01-01", "include_ongoing": "false",
      "doctor": [A, B], "patient": B, "order": "-start_time", "status": "finished"}),
    ("list_calendar_absences", (A,), {"end_date": "2026-01-31", "show_all": True}, "GET",
     f"/v4/clinics/{A}/calendar_absences",
     {"limit": 50, "offset": 0, "end_date": "2026-01-31", "show_all": "true"}),
    ("get_calendar_absence", (A,), {}, "GET", f"/v4/calendar_absences/{A}", {}),
    ("list_calendar_opening_hours", (A,), {}, "GET",
     f"/v4/clinics/{A}/calendar_opening_hours", {"limit": 50, "offset": 0}),
    ("get_calendar_opening_hour", (A,), {}, "GET", f"/v4/calendar_opening_hours/{A}", {}),
    ("list_appointment_rooms", (A,), {}, "GET", f"/v4/clinics/{A}/appointment_rooms",
     {"limit": 50, "offset": 0}),
    ("get_appointment_room", (A,), {}, "GET", f"/v4/appointment_rooms/{A}", {}),
    ("list_appointment_devices", (A,), {}, "GET", f"/v4/clinics/{A}/appointment_devices",
     {"limit": 50, "offset": 0}),
    ("get_appointment_device", (A,), {}, "GET", f"/v4/appointment_devices/{A}", {}),
    ("list_treatment_pricing_distributions", (A,), {}, "GET",
     f"/v4/treatment_pricings/{A}/user_accounting_distributions", {}),
    ("list_treatment_packages", (A,), {"search": "zzz"}, "GET",
     f"/v4/clinics/{A}/treatment_packages", {"limit": 50, "offset": 0, "search": "zzz"}),
    ("get_treatment_package", (A,), {}, "GET", f"/v4/treatment_packages/{A}", {}),
    ("list_treatment_package_items", (A,), {"limit": 5}, "GET",
     f"/v4/treatment_packages/{A}/items", {"limit": 5, "offset": 0}),
    ("list_treatment_package_distributions", (A,), {}, "GET",
     f"/v4/treatment_packages/{A}/user_accounting_distributions", {}),
    ("list_accounting_distributions", (A,), {}, "GET",
     f"/v4/clinics/{A}/accounting_distributions", {"limit": 50, "offset": 0}),
    ("get_accounting_distribution", (A,), {}, "GET", f"/v4/accounting_distributions/{A}",
     {}),
    ("list_global_products", (A,), {"search": "zzz"}, "GET",
     f"/v4/clinics/{A}/global_products", {"limit": 50, "offset": 0, "search": "zzz"}),
    ("list_payments", (A,), {"invoice_id": B}, "GET", f"/v4/clinics/{A}/payments",
     {"limit": 50, "offset": 0, "invoice": B}),
    ("get_payment", (A,), {}, "GET", f"/v4/payments/{A}", {}),
    ("list_payment_mediums", (A,), {}, "GET", f"/v4/clinics/{A}/payment_mediums",
     {"limit": 50, "offset": 0}),
    ("get_payment_medium", (A,), {}, "GET", f"/v4/payment_mediums/{A}", {}),
    ("get_appointment_income_statistics", (A,),
     {"start_date": "2026-01-01", "end_date": "2026-06-30", "period_type": "week"}, "GET",
     f"/v4/clinics/{A}/statistics/appointment_income",
     {"start_time": "2026-01-01", "end_time": "2026-06-30", "period_type": "week"}),
    ("list_treatment_type_statistics", (A,), {}, "GET",
     f"/v4/clinics/{A}/statistics/treatment_types", {}),
    ("list_treatment_type_income_statistics", (A,), {"period_type": "year"}, "GET",
     f"/v4/clinics/{A}/statistics/treatment_types/income", {"period_type": "year"}),
    ("get_patient_stats", (A,), {}, "GET", f"/v4/patients/{A}/stats", {}),
    ("list_leads", (A,), {}, "GET", f"/v4/clinics/{A}/leads", {"limit": 50, "offset": 0}),
    ("get_lead", (A,), {}, "GET", f"/v4/leads/{A}", {}),
    ("list_object_labels", (A,), {"types": ["lead_source", "quote_tag"]}, "GET",
     f"/v4/clinics/{A}/object_labels",
     {"limit": 50, "offset": 0, "type": ["lead_source", "quote_tag"]}),
    ("list_communication_templates", (A,), {"kind": "sms"}, "GET",
     f"/v4/clinics/{A}/communication_templates", {"limit": 50, "offset": 0, "kind": "sms"}),
    ("get_communication_template", (A,), {}, "GET", f"/v4/communication_templates/{A}",
     {}),
    ("list_document_templates", (A,), {"type": 0, "master_id": B}, "GET",
     f"/v4/clinics/{A}/document_templates",
     {"limit": 50, "offset": 0, "type": 0, "master": B}),
    ("get_document_template", (A,), {}, "GET", f"/v4/document_templates/{A}", {}),
    ("list_webhooks", (A,), {}, "GET", f"/v4/clinics/{A}/webhooks",
     {"limit": 50, "offset": 0}),
    ("get_webhook", (A,), {}, "GET", f"/v4/webhooks/{A}", {}),
])
def test_read_paths_and_params(calls, client, fn, args, kwargs, method, path, params):
    getattr(client, fn)(*args, **kwargs)
    call = calls[0]
    assert (call["method"], call["url"], call["params"]) == (method, f"{BASE}{path}", params)
    assert call["json"] is None


@pytest.mark.parametrize("fn,kwargs,match", [
    ("list_calendar_journeys", {"order": "patient_name"}, "order"),
    ("list_calendar_journeys", {"order": "-patient_name"}, "order"),
    ("list_calendar_journeys", {"doctor_ids": A}, "liste"),
    ("list_calendar_journeys", {"visit_type_ids": ["42"]}, "UUID"),
    ("list_object_labels", {"types": "lead_source"}, "liste"),
    ("get_appointment_income_statistics", {"period_type": "quarter"}, "period_type"),
    ("list_payments", {"invoice_id": "../x"}, "UUID"),
])
def test_bad_filters_are_refused_before_the_request(calls, client, fn, kwargs, match):
    with pytest.raises(ValueError, match=match):
        getattr(client, fn)(A, **kwargs)
    assert not calls


def test_no_name_search_is_exposed_where_the_api_matches_on_a_person():
    import inspect

    for fn in ("list_leads", "list_calendar_journeys"):
        assert "search" not in inspect.signature(getattr(NextmotionClient, fn)).parameters


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
