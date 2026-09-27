"""Lectures de base GoCardless (creditors, payments, mandates, customers,
events) : un refus amont LÈVE, il ne se lit jamais comme une liste ou un dict
vide — otomata-tech/oto#250 (le garde `isinstance(dict)` de `failed_payments`
ne se déclenchait jamais parce que ces lectures rendaient `[]`/`{}` sur un 401).
"""
import pytest

from oto.tools.common.errors import UpstreamHTTPError
from oto.tools.gocardless.client import GoCardlessClient


def _client(reponses):
    """Client dont `fetch` rend, dans l'ordre, les réponses données."""
    c = GoCardlessClient(api_key="k", rate_limit_delay=0)
    c.appels = []
    file = list(reponses)

    def fetch(endpoint, params=None, retries=3):
        c.appels.append((endpoint, dict(params or {})))
        return file.pop(0)

    c.fetch = fetch
    return c


REFUS_401 = {"error": "401", "status_code": 401, "details": "invalid token"}


def test_list_creditors_rend_la_liste():
    c = _client([{"creditors": [{"id": "CR1"}]}])
    assert c.list_creditors() == [{"id": "CR1"}]


def test_list_creditors_leve_sur_un_refus_amont():
    c = _client([REFUS_401])
    with pytest.raises(UpstreamHTTPError) as e:
        c.list_creditors()
    assert e.value.status_code == 401


def test_list_payments_transmet_les_filtres_et_rend_la_liste():
    c = _client([{"payments": [{"id": "PM1"}]}])
    rows = c.list_payments(status="failed", limit=10, mandate="MD1",
                           customer="CU1", created_gt="2026-05-25")
    assert rows == [{"id": "PM1"}]
    assert c.appels == [("payments", {
        "limit": 10, "status": "failed", "mandate": "MD1", "customer": "CU1",
        "created_at[gt]": "2026-05-25T00:00:00.000Z",
    })]


def test_list_payments_leve_sur_un_refus_amont():
    c = _client([REFUS_401])
    with pytest.raises(UpstreamHTTPError):
        c.list_payments(status="failed")


def test_get_payment_rend_le_paiement():
    c = _client([{"payments": {"id": "PM1", "amount": 100}}])
    assert c.get_payment("PM1") == {"id": "PM1", "amount": 100}


def test_get_payment_leve_sur_un_refus_amont():
    c = _client([REFUS_401])
    with pytest.raises(UpstreamHTTPError):
        c.get_payment("PM1")


def test_get_mandate_rend_le_mandat():
    c = _client([{"mandates": {"id": "MD1", "status": "active"}}])
    assert c.get_mandate("MD1") == {"id": "MD1", "status": "active"}


def test_get_mandate_leve_sur_un_refus_amont():
    c = _client([REFUS_401])
    with pytest.raises(UpstreamHTTPError):
        c.get_mandate("MD1")


def test_get_customer_rend_le_client():
    c = _client([{"customers": {"id": "CU1", "email": "a@b.fr"}}])
    assert c.get_customer("CU1") == {"id": "CU1", "email": "a@b.fr"}


def test_get_customer_leve_sur_un_refus_amont():
    c = _client([REFUS_401])
    with pytest.raises(UpstreamHTTPError):
        c.get_customer("CU1")


def test_list_events_transmet_les_filtres_et_rend_la_liste():
    c = _client([{"events": [{"id": "EV1", "action": "failed"}]}])
    rows = c.list_events(payment="PM1", action="failed", limit=5)
    assert rows == [{"id": "EV1", "action": "failed"}]
    assert c.appels == [("events", {"limit": 5, "payment": "PM1", "action": "failed"})]


def test_list_events_leve_sur_un_refus_amont():
    c = _client([REFUS_401])
    with pytest.raises(UpstreamHTTPError):
        c.list_events(payment="PM1")


def test_payment_party_leve_sur_un_refus_amont_au_lieu_de_rendre_un_dict_d_erreur():
    """Le contre-exemple de l'issue : avant `_read`, `get_payment` rendait
    `{}` sur un refus, et `payment_party` renvoyait ce dict d'erreur tel quel
    (`if "error" in p: return p`), donc un token mort produisait un résultat
    qui RESSEMBLE à un paiement introuvable plutôt qu'un refus."""
    c = _client([REFUS_401])
    with pytest.raises(UpstreamHTTPError):
        c.payment_party("PM1")
