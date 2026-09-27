"""Versements GoCardless (payouts) : liste, détail et lignes, en lecture.

Contrat amont : `GET /payouts` (filtres `status`, `currency`, `reference`,
`created_at[gt|lt]`), `GET /payouts/{id}`, `GET /payout_items?payout=…`
paginé par `meta.cursors.after`. Un refus amont LÈVE : une liste vide ne doit
jamais vouloir dire « la clé a été refusée ».
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


def test_liste_des_versements_transmet_les_filtres_au_format_de_l_api():
    c = _client([{"payouts": [{"id": "PO1", "amount": 12345}], "meta": {}}])
    rows = c.list_payouts(status="paid", limit=100, currency="EUR", reference="REF-1",
                          created_gt="2026-08-01", created_lt="2026-09-01T00:00:00Z")
    assert rows == [{"id": "PO1", "amount": 12345}]
    assert c.appels == [("payouts", {
        "limit": 100, "status": "paid", "currency": "EUR", "reference": "REF-1",
        "created_at[gt]": "2026-08-01T00:00:00.000Z",
        "created_at[lt]": "2026-09-01T00:00:00Z",
    })]


def test_liste_sans_filtre_n_envoie_que_la_taille_de_page():
    c = _client([{"payouts": []}])
    assert c.list_payouts() == []
    assert c.appels == [("payouts", {"limit": 50})]


def test_un_refus_amont_leve_au_lieu_de_rendre_une_liste_vide():
    c = _client([{"error": "401", "status_code": 401, "details": "invalid token"}])
    with pytest.raises(UpstreamHTTPError) as e:
        c.list_payouts()
    assert e.value.status_code == 401 and "gocardless" in str(e.value)


def test_une_erreur_reseau_leve_aussi():
    c = _client([{"error": "Connection reset"}])
    with pytest.raises(RuntimeError, match="payouts/PO1"):
        c.get_payout("PO1")


def test_les_lignes_d_un_versement_suivent_le_curseur_jusqu_au_bout():
    c = _client([
        {"payout_items": [{"type": "payment_paid_out", "amount": 1000,
                           "links": {"payment": "PM1"}}],
         "meta": {"cursors": {"after": "CUR1"}}},
        {"payout_items": [{"type": "gocardless_fee", "amount": -20,
                           "links": {"payment": "PM1"}}],
         "meta": {"cursors": {"after": None}}},
    ])
    items = c.list_payout_items("PO1")
    assert [i["type"] for i in items] == ["payment_paid_out", "gocardless_fee"]
    assert c.appels == [
        ("payout_items", {"payout": "PO1", "limit": 500}),
        ("payout_items", {"payout": "PO1", "limit": 500, "after": "CUR1"}),
    ]


def test_un_refus_en_cours_de_pagination_ne_rend_pas_une_collecte_tronquee():
    c = _client([
        {"payout_items": [{"type": "payment_paid_out"}],
         "meta": {"cursors": {"after": "CUR1"}}},
        {"error": "410", "status_code": 410, "details": "gone"},
    ])
    with pytest.raises(UpstreamHTTPError) as e:
        c.list_payout_items("PO1")
    assert e.value.status_code == 410


def test_detail_d_un_versement_rend_le_versement_et_toutes_ses_lignes():
    c = _client([
        {"payouts": {"id": "PO1", "amount": 980, "deducted_fees": 20}},
        {"payout_items": [{"type": "payment_paid_out", "amount": 1000},
                          {"type": "gocardless_fee", "amount": -20}],
         "meta": {"cursors": {}}},
    ])
    d = c.payout_detail("PO1")
    assert d["payout"]["id"] == "PO1"
    assert sum(i["amount"] for i in d["items"]) == d["payout"]["amount"]
    assert [a[0] for a in c.appels] == ["payouts/PO1", "payout_items"]
